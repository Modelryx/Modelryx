using System.Collections.Concurrent;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.Json;

namespace Modelryx.LocalCad.Agent;

internal static class Program
{
    private const string Prefix = "http://127.0.0.1:47832/";
    private static readonly ConcurrentDictionary<Guid, SelectionJob> Selections = new();
    private static readonly SemaphoreSlim SelectionLock = new(1, 1);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly HashSet<string> AllowedOrigins = BuildAllowedOrigins();
    private static readonly string DataRoot = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Modelryx", "LocalCadAgent");

    [STAThread]
    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 3 && args[0].Equals("--analyze", StringComparison.OrdinalIgnoreCase))
        {
            var input = Path.GetFullPath(args[1]);
            var output = Path.GetFullPath(args[2]);
            Directory.CreateDirectory(output);
            var analysis = await CadAnalyzer.AnalyzeAsync(input, output, CancellationToken.None);
            Console.WriteLine(JsonSerializer.Serialize(analysis.Report, JsonOptions));
            return 0;
        }
        Directory.CreateDirectory(DataRoot);
        PruneOldSelections();
        using var listener = new HttpListener();
        listener.Prefixes.Add(Prefix);
        try { listener.Start(); }
        catch (HttpListenerException error)
        {
            Console.Error.WriteLine($"Modelryx Local CAD Agent could not listen on {Prefix}: {error.Message}");
            return 1;
        }
        Console.WriteLine($"Modelryx Local CAD Agent listening on {Prefix}");
        while (listener.IsListening)
        {
            var context = await listener.GetContextAsync();
            _ = Task.Run(() => HandleAsync(context));
        }
        return 0;
    }

    private static async Task HandleAsync(HttpListenerContext context)
    {
        try
        {
            if (!AuthorizeOrigin(context))
            {
                await JsonAsync(context, 403, new { error = "This web origin is not allowed." });
                return;
            }
            AddCors(context);
            if (context.Request.HttpMethod == "OPTIONS")
            {
                context.Response.StatusCode = 204;
                context.Response.Close();
                return;
            }
            var path = context.Request.Url?.AbsolutePath.TrimEnd('/') ?? "";
            if (context.Request.HttpMethod == "GET" && path == "/health")
            {
                await JsonAsync(context, 200, new { status = "ok", contract_version = "1.0" });
                return;
            }
            if (context.Request.HttpMethod == "POST" && path == "/api/v1/selections")
            {
                await SelectAsync(context);
                return;
            }
            var segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
            if (context.Request.HttpMethod == "GET" && segments.Length == 4
                && segments[0] == "api" && segments[1] == "v1" && segments[2] == "selections"
                && Guid.TryParse(segments[3], out var statusId))
            {
                await SelectionStatusAsync(context, statusId);
                return;
            }
            if (context.Request.HttpMethod == "GET" && segments.Length == 5
                && segments[0] == "api" && segments[1] == "v1" && segments[2] == "selections"
                && Guid.TryParse(segments[3], out var id) && segments[4] == "package")
            {
                await PackageAsync(context, id);
                return;
            }
            await JsonAsync(context, 404, new { error = "Not found." });
        }
        catch (Exception error)
        {
            await JsonAsync(context, 500, new { error = error.Message });
        }
    }

    private static async Task SelectAsync(HttpListenerContext context)
    {
        if (context.Request.ContentLength64 > 65536)
        {
            await JsonAsync(context, 413, new { error = "The worker format list is too large." });
            return;
        }
        WorkerFormat[] formats = [];
        if (context.Request.HasEntityBody)
        {
            using var buffer = new MemoryStream();
            var chunk = new byte[4096];
            int count;
            while ((count = await context.Request.InputStream.ReadAsync(chunk)) > 0)
            {
                if (buffer.Length + count > 65536) throw new InvalidDataException("The worker format list is too large.");
                buffer.Write(chunk, 0, count);
            }
            var body = Encoding.UTF8.GetString(buffer.ToArray());
            formats = JsonSerializer.Deserialize<WorkerFormat[]>(body, JsonOptions) ?? [];
            if (formats.Length > 100 || formats.Any(worker => worker is null ||
                string.IsNullOrWhiteSpace(worker.Id) || string.IsNullOrWhiteSpace(worker.CadSystem) ||
                worker.SourceExtensions is null || worker.SourceExtensions.Length > 32 ||
                worker.SourceExtensions.Any(extension => !System.Text.RegularExpressions.Regex.IsMatch(extension ?? "", @"^\.[A-Za-z0-9]{1,16}$"))))
                throw new InvalidDataException("The registered worker format list is invalid.");
        }
        if (!await SelectionLock.WaitAsync(0))
        {
            await JsonAsync(context, 409, new { error = "A CAD selection request is already starting." });
            return;
        }
        try
        {
            var active = Selections.Values
                .Where(selection => !selection.Analysis.IsCompleted)
                .OrderByDescending(selection => selection.CreatedAt)
                .FirstOrDefault();
            if (active is not null)
            {
                var stopped = false;
                try
                {
                    active.Cancellation.Cancel();
                    try { await active.Analysis.WaitAsync(TimeSpan.FromSeconds(15)); }
                    catch (TimeoutException)
                    {
                        throw new InvalidOperationException(
                            "The previous CAD analyzer did not stop safely. Restart the Local CAD Agent before trying again.");
                    }
                    catch (Exception) { }
                    stopped = true;
                }
                finally
                {
                    if (stopped || active.Analysis.IsCompleted)
                    {
                        Selections.TryRemove(active.Id, out _);
                        active.Cancellation.Dispose();
                        TryDeleteWorkspace(active.Workspace);
                    }
                }
            }

            var selectedFile = await NativeFilePicker.PickCadFileAsync(formats);
            if (selectedFile is null)
            {
                await JsonAsync(context, 200, new { cancelled = true });
                return;
            }
            var id = Guid.NewGuid();
            var root = selectedFile.Path;
            var worker = selectedFile.Worker;
            var workspace = Path.Combine(DataRoot, id.ToString("N"));
            Directory.CreateDirectory(workspace);
            var cancellation = new CancellationTokenSource();
            var analysis = AnalyzeSelectionAsync(root, workspace, cancellation.Token, worker);
            Selections[id] = new SelectionJob(
                id, DateTimeOffset.UtcNow, workspace, cancellation, analysis, worker);
            await JsonAsync(context, 202, new
            {
                selection_id = id,
                status = "analyzing",
                status_url = $"{Prefix}api/v1/selections/{id}",
            });
        }
        finally { SelectionLock.Release(); }
    }

    private static async Task<AnalysisOutput> AnalyzeSelectionAsync(
        string root, string workspace, CancellationToken cancellationToken, WorkerFormat? worker)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromMinutes(15));
        var output = await CadAnalyzer.AnalyzeAsync(root, workspace, deadline.Token, worker);
        ValidatePackage(output.PackagePath);
        return output;
    }

    private static async Task SelectionStatusAsync(HttpListenerContext context, Guid id)
    {
        if (!Selections.TryGetValue(id, out var selection))
        {
            await JsonAsync(context, 404, new { error = "The local CAD analysis was not found or has expired." });
            return;
        }
        if (!selection.Analysis.IsCompleted)
        {
            var progress = ReadAnalysisStatus(selection.Workspace);
            await JsonAsync(context, 202, new
            {
                selection_id = id,
                status = "analyzing",
                stage = progress?.Stage,
                message = progress?.Message ?? "Analyzing references with the installed CAD system…",
                updated_at = progress?.UpdatedAt,
            });
            return;
        }
        if (!selection.Analysis.IsCompletedSuccessfully)
        {
            Selections.TryRemove(id, out _);
            selection.Cancellation.Dispose();
            TryDeleteWorkspace(selection.Workspace);
            var error = selection.Analysis.IsCanceled
                ? "Local CAD analysis timed out."
                : selection.Analysis.Exception?.GetBaseException().Message ?? "Local CAD analysis failed.";
            await JsonAsync(context, 500, new { error });
            return;
        }
        var output = await selection.Analysis;
        await JsonAsync(context, 200, new
        {
            selection_id = id,
            status = "complete",
            report = output.Report,
            worker_id = selection.Worker?.Id,
            cad_system = selection.Worker?.CadSystem,
            package_url = $"{Prefix}api/v1/selections/{id}/package",
        });
    }

    private static async Task PackageAsync(HttpListenerContext context, Guid id)
    {
        if (!Selections.TryGetValue(id, out var selection) || !selection.Analysis.IsCompletedSuccessfully)
        {
            await JsonAsync(context, 404, new { error = "The local CAD package expired or was already downloaded." });
            return;
        }
        var output = await selection.Analysis;
        if (!File.Exists(output.PackagePath))
        {
            if (Selections.TryRemove(id, out _))
            {
                selection.Cancellation.Dispose();
                TryDeleteWorkspace(selection.Workspace);
            }
            await JsonAsync(context, 404, new { error = "The local CAD package expired or was already downloaded." });
            return;
        }
        context.Response.StatusCode = 200;
        context.Response.ContentType = "application/zip";
        context.Response.Headers["Cache-Control"] = "no-store";
        context.Response.ContentLength64 = new FileInfo(output.PackagePath).Length;
        await using (var input = File.OpenRead(output.PackagePath))
            await input.CopyToAsync(context.Response.OutputStream);
        context.Response.Close();
        if (Selections.TryRemove(id, out _))
        {
            selection.Cancellation.Dispose();
            TryDeleteWorkspace(selection.Workspace);
        }
    }

    private static void TryDeleteWorkspace(string workspace)
    {
        try
        {
            Directory.Delete(workspace, true);
        }
        catch (UnauthorizedAccessException)
        {
            // File.Copy preserves the source attributes. CAD files are often read-only, and on
            // Windows one such file prevents Directory.Delete(recursive: true) from removing the
            // entire workspace. Clear only the read-only bit inside our private workspace and
            // retry; cleanup must never keep the agent from starting or serving the next request.
            TryClearReadOnlyAttributes(workspace);
            try { Directory.Delete(workspace, true); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
        catch (IOException) { }
    }

    private static void TryClearReadOnlyAttributes(string workspace)
    {
        try
        {
            foreach (var path in Directory.EnumerateFileSystemEntries(
                         workspace, "*", SearchOption.AllDirectories))
            {
                try { File.SetAttributes(path, File.GetAttributes(path) & ~FileAttributes.ReadOnly); }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            }
            File.SetAttributes(workspace, File.GetAttributes(workspace) & ~FileAttributes.ReadOnly);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }

    private static AnalysisStatus? ReadAnalysisStatus(string workspace)
    {
        var path = Path.Combine(workspace, "analysis-status.json");
        if (!File.Exists(path)) return null;
        try
        {
            return JsonSerializer.Deserialize<AnalysisStatus>(File.ReadAllText(path), JsonOptions);
        }
        catch (Exception error) when (error is IOException or JsonException) { return null; }
    }

    private static void ValidatePackage(string packagePath)
    {
        const int maxEntries = 5000;
        const long maxBytes = 2L * 1024 * 1024 * 1024;
        using var archive = ZipFile.OpenRead(packagePath);
        if (archive.Entries.Count > maxEntries) throw new InvalidDataException("CAD package has too many files.");
        long total = 0;
        var seen = new HashSet<string>(OperatingSystem.IsWindows()
            ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);
        foreach (var entry in archive.Entries)
        {
            var path = entry.FullName.Replace('\\', '/');
            if (path.StartsWith('/') || path.Split('/').Contains("..") || !seen.Add(path))
                throw new InvalidDataException($"CAD analyzer created an unsafe package path: {path}");
            total += entry.Length;
            if (total > maxBytes) throw new InvalidDataException("CAD package exceeds the 2 GB local limit.");
        }
    }

    private static bool AuthorizeOrigin(HttpListenerContext context)
    {
        var origin = context.Request.Headers["Origin"];
        return string.IsNullOrWhiteSpace(origin) || AllowedOrigins.Contains(origin.TrimEnd('/'));
    }

    private static void AddCors(HttpListenerContext context)
    {
        var origin = context.Request.Headers["Origin"];
        if (!string.IsNullOrWhiteSpace(origin) && AllowedOrigins.Contains(origin.TrimEnd('/')))
        {
            context.Response.Headers["Access-Control-Allow-Origin"] = origin;
            context.Response.Headers["Vary"] = "Origin";
            context.Response.Headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
            context.Response.Headers["Access-Control-Allow-Headers"] = "Content-Type";
        }
        context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    }

    private static async Task JsonAsync(HttpListenerContext context, int status, object value)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, JsonOptions));
        context.Response.StatusCode = status;
        context.Response.ContentType = "application/json";
        context.Response.ContentLength64 = bytes.Length;
        await context.Response.OutputStream.WriteAsync(bytes);
        context.Response.Close();
    }

    private static HashSet<string> BuildAllowedOrigins()
    {
        const string localOrigins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000";
        var environment = Environment.GetEnvironmentVariable("MODELRYX_ALLOWED_ORIGINS");
        var configured = string.IsNullOrWhiteSpace(environment)
            ? AgentConfiguration.Load().AllowedOrigins.Append(localOrigins)
            : [environment];
        return configured.SelectMany(value => value.Split(',',
                StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            .Select(value => value.TrimEnd('/')).ToHashSet(StringComparer.OrdinalIgnoreCase);
    }

    private static void PruneOldSelections()
    {
        foreach (var directory in Directory.EnumerateDirectories(DataRoot))
            try
            {
                if (Directory.GetLastWriteTimeUtc(directory) < DateTime.UtcNow.AddHours(-24))
                    TryDeleteWorkspace(directory);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }
}

internal sealed record AnalysisStatus(string Stage, string Message, DateTimeOffset UpdatedAt);
