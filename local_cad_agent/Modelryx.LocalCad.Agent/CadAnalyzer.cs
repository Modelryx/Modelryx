using System.Diagnostics;
using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Nodes;

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
            return ParseNativeReport(json, workDirectory, local.Version);
        }
        throw new InvalidOperationException("Select an enabled external CAD worker and install its local preprocessor before opening this file.");
    }

    private static AnalysisOutput ParseNativeReport(string outputText, string outputDirectory,
        string fallbackAnalyzer)
    {
        var node = ParseNativeReportNode(outputText);
        node["contract_version"] ??= "1.0";
        node["analyzer_version"] ??= fallbackAnalyzer;
        node["missing_references"] ??= new JsonArray();
        node["references"] ??= new JsonArray();
        var report = node.Deserialize<DependencyReport>(JsonOptions)
            ?? throw new InvalidDataException("The CAD analyzer report is incomplete.");
        if (report.MissingReferences.Count > 0)
            throw new InvalidDataException("Repair missing CAD references before upload: " + string.Join("; ",
                report.MissingReferences.Take(20).Select(value => $"{value.Reference ?? "unknown reference"} (referenced by {value.ReferencedBy})")));
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
