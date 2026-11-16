using System.Diagnostics;
using System.ComponentModel;
using System.IO.Compression;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;

namespace Modelryx.LocalCad.Agent;

internal static class CadAnalyzer
{
    private static AgentConfiguration Configuration => AgentConfiguration.Load();
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };

    public static async Task<AnalysisOutput> AnalyzeAsync(string root, string workDirectory,
        CancellationToken cancellationToken, WorkerFormat? externalWorker = null)
    {
        var extension = Path.GetExtension(root).ToLowerInvariant();
        if (externalWorker is not null)
        {
            var configuration = Configuration;
            var local = configuration.FindExternal(externalWorker.Id, externalWorker.CadSystem, extension)
                ?? throw new InvalidOperationException($"Install the local preprocessor for '{externalWorker.WorkerName}' before opening this file.");
            var executable = configuration.RequireExternalExecutable(local);
            var json = await RunAsync(executable, [root, workDirectory], cancellationToken);
            return ParseNativeReport(json, workDirectory, local.Version, root);
        }
        return extension switch
        {
            ".fcstd" => await AnalyzeFreeCadAsync(root, workDirectory, cancellationToken),
            _ => throw new NotSupportedException($"{extension} is not a supported CAD document."),
        };
    }

    private static async Task<AnalysisOutput> AnalyzeFreeCadAsync(string root, string output,
        CancellationToken cancellationToken)
    {
        var python = Configuration.FindFreeCadCommand();
        if (string.IsNullOrWhiteSpace(python) || !File.Exists(python))
            throw new InvalidOperationException(
                "FreeCAD was not found. Install FreeCAD or set MODELRYX_FREECAD_CMD to its Python executable.");
        var script = Configuration.RequireFreeCadPreprocessor();
        var json = await RunAsync(python, [script, root, output], cancellationToken);
        var reportNode = ParseNativeReportNode(json);
        var packageValue = reportNode["package"]?.GetValue<string>();
        var rootLogicalPath = reportNode["root_logical_path"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(packageValue) || string.IsNullOrWhiteSpace(rootLogicalPath))
            throw new InvalidDataException("The FreeCAD analyzer returned no isolated package root.");
        var packagedRoot = ExtractPackageForMetadata(
            Path.GetFullPath(packageValue), rootLogicalPath, Path.Combine(output, "metadata-package"));
        var metadataScript = Configuration.RequireFreeCadConverter();
        var metadataPath = Path.Combine(output, "part-metadata.json");
        await RunAsync(python,
            [metadataScript, packagedRoot, "--metadata-only", "--metadata-output", metadataPath],
            cancellationToken);
        return ParseNativeReport(json, output, "modelryx-freecad-dependencies-1", packagedRoot);
    }

    private static string PackagePath(string staging, string logicalPath) => Path.GetFullPath(
        Path.Combine(staging, logicalPath.Replace('/', Path.DirectorySeparatorChar)));

    private static bool IsWithin(string file, string directory)
    {
        var relative = Path.GetRelativePath(directory, file);
        return relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar,
            StringComparison.Ordinal) && !Path.IsPathRooted(relative);
    }

    private static AnalysisOutput ParseNativeReport(string outputText, string outputDirectory,
        string fallbackAnalyzer, string rootPath)
    {
        var node = ParseNativeReportNode(outputText);
        node["contract_version"] ??= "1.0";
        node["analyzer_version"] ??= fallbackAnalyzer;
        node["missing_references"] ??= new JsonArray();
        node["references"] ??= new JsonArray();
        var metadataPath = Path.Combine(outputDirectory, "part-metadata.json");
        if (File.Exists(metadataPath))
        {
            node["part_metadata"] = JsonNode.Parse(File.ReadAllText(metadataPath));
            node["metadata_source_sha256"] = Sha256(rootPath);
        }
        var report = node.Deserialize<DependencyReport>(JsonOptions)
            ?? throw new InvalidDataException("The CAD analyzer report is incomplete.");
        var packageValue = node["package"]?.GetValue<string>();
        var package = string.IsNullOrWhiteSpace(packageValue)
            ? Path.Combine(outputDirectory, "cad-package.zip")
            : Path.GetFullPath(packageValue);
        if (!File.Exists(package))
            throw new InvalidDataException("The CAD analyzer did not create cad-package.zip.");
        if (!Path.GetFullPath(package).StartsWith(Path.GetFullPath(outputDirectory) + Path.DirectorySeparatorChar,
                OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            throw new InvalidDataException("The CAD analyzer returned a package outside its assigned workspace.");
        PackageContractValidator.Validate(report, package);
        return new AnalysisOutput(report, package);
    }

    private static JsonObject ParseNativeReportNode(string outputText)
    {
        var jsonLine = outputText.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries)
            .LastOrDefault(line => line.TrimStart().StartsWith('{'))
            ?? throw new InvalidDataException("The CAD analyzer returned no dependency report.");
        return JsonNode.Parse(jsonLine)?.AsObject()
            ?? throw new InvalidDataException("The CAD analyzer returned invalid JSON.");
    }

    private static string ExtractPackageForMetadata(
        string package, string rootLogicalPath, string destinationDirectory)
    {
        Directory.CreateDirectory(destinationDirectory);
        using var archive = ZipFile.OpenRead(package);
        foreach (var entry in archive.Entries)
        {
            if (string.IsNullOrEmpty(entry.Name)) continue;
            var destination = PackagePath(destinationDirectory, entry.FullName);
            if (!IsWithin(destination, destinationDirectory))
                throw new InvalidDataException("The CAD package contains an unsafe path.");
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            entry.ExtractToFile(destination, overwrite: true);
        }
        var root = PackagePath(destinationDirectory, rootLogicalPath);
        if (!IsWithin(root, destinationDirectory) || !File.Exists(root))
            throw new InvalidDataException("The isolated CAD package does not contain its declared root file.");
        return root;
    }

    private static string Sha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static async Task<string> RunAsync(string executable, IEnumerable<string> arguments,
        CancellationToken cancellationToken, IReadOnlyDictionary<string, string>? environment = null,
        string? workingDirectory = null)
    {
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        if (workingDirectory is not null) start.WorkingDirectory = workingDirectory;
        foreach (var (name, value) in environment ?? new Dictionary<string, string>()) start.Environment[name] = value;
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        using var process = Process.Start(start)
            ?? throw new InvalidOperationException($"Could not start {Path.GetFileName(executable)}.");
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        try
        {
            await process.WaitForExitAsync(cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await StopProcessAsync(process);
            throw new TimeoutException(
                $"{Path.GetFileName(executable)} did not finish within 15 minutes and was stopped. " +
                "Close any CAD warning, licensing, or file-resolution dialogs and try again.");
        }
        var output = await stdout;
        var error = await stderr;
        if (process.ExitCode != 0)
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(error)
                ? $"{Path.GetFileName(executable)} exited with code {process.ExitCode}."
                : error.Trim());
        return output;
    }

    private static async Task StopProcessAsync(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception or NotSupportedException) { }
        try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(10)); }
        catch (Exception error) when (error is InvalidOperationException or TimeoutException) { }
    }

}
