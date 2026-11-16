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
            try
            {
                var json = await RunAsync(executable, [root, workDirectory], cancellationToken);
                return ParseNativeReport(json, workDirectory, local.Version, root);
            }
            finally { await StopOwnedInventorAsync(workDirectory); }
        }
        return extension switch
        {
            ".iam" or ".ipt" or ".idw" or ".ipn" => await AnalyzeInventorAsync(
                root, workDirectory, cancellationToken),
            ".fcstd" => await AnalyzeFreeCadAsync(root, workDirectory, cancellationToken),
            ".dwg" or ".dxf" => await AnalyzeAutoCadAsync(
                root, workDirectory, cancellationToken),
            ".prt" => await AnalyzeNxAsync(root, workDirectory, cancellationToken),
            _ => throw new NotSupportedException($"{extension} is not a supported CAD document."),
        };
    }

    private static async Task<AnalysisOutput> AnalyzeNxAsync(string root, string output, CancellationToken token)
    {
        RequireWindows("Siemens NX");
        var nx = Configuration.RequireNxDirectory();
        var executable = Configuration.RequireNxPreprocessor();
        var diagnostics = Path.Combine(output, "nx-diagnostics");
        Directory.CreateDirectory(diagnostics);
        var environment = new Dictionary<string, string>
        {
            ["UGII_BASE_DIR"] = nx,
            ["MODELRYX_NXOPEN_DIR"] = Path.Combine(nx, "NXBIN", "managed_core"),
            ["PATH"] = Path.Combine(nx, "NXBIN") + Path.PathSeparator + Environment.GetEnvironmentVariable("PATH"),
            ["UGII_TMP_DIR"] = diagnostics, ["TEMP"] = diagnostics, ["TMP"] = diagnostics,
        };
        var package = Path.Combine(output, "cad-package.zip");
        await RunAsync(executable, [Path.GetFullPath(root), package, "--discover-references"], token, environment, output);
        // A second process guarantees that cached original parts cannot satisfy staged references.
        var report = await RunAsync(executable, ["--validate-package", package, Path.Combine(output, "nx-reopen")],
            token, environment, output);
        return ParseNativeReport(report, output, "modelryx-nx-dependencies-1", root);
    }

    private static async Task<AnalysisOutput> AnalyzeInventorAsync(string root, string output,
        CancellationToken cancellationToken)
    {
        RequireWindows("Autodesk Inventor");
        var executable = Configuration.RequireInventorPreprocessor();
        try
        {
            var json = await RunAsync(executable, [root, output], cancellationToken);
            return ParseNativeReport(json, output, "modelryx-inventor-dependencies-1", root);
        }
        finally
        {
            await StopOwnedInventorAsync(output);
        }
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

    private static async Task<AnalysisOutput> AnalyzeAutoCadAsync(string root, string output,
        CancellationToken cancellationToken)
    {
        RequireWindows("Autodesk AutoCAD");
        var console = Configuration.FindAutoCadCoreConsole();
        if (!File.Exists(console))
            throw new InvalidOperationException(
                "AutoCAD Core Console was not found. Install AutoCAD or set MODELRYX_AUTOCAD_CORE_CONSOLE.");
        var plugin = Configuration.RequireAutoCadPreprocessor();
        var script = Path.Combine(output, "modelryx-analyze.scr");
        await File.WriteAllLinesAsync(script,
        [
            "FILEDIA", "0", "SECURELOAD", "0", "_.NETLOAD", plugin.Replace('\\', '/'),
            "MODELRYXPREPROCESSANALYZE", output.Replace('\\', '/'), "_.QUIT", "_N",
        ], cancellationToken);
        var consoleOutput = await RunAsync(
            console, ["/i", root, "/s", script, "/l", "en-US"], cancellationToken);
        var manifestPath = Path.Combine(output, "xref-dependencies.json");
        if (!File.Exists(manifestPath))
            throw new InvalidDataException(
                "The Modelryx AutoCAD preprocessor did not complete dependency analysis. " +
                "AutoCAD may report a successful process exit even when NETLOAD fails. " +
                AutoCadDiagnostic(consoleOutput));
        var manifest = JsonNode.Parse(await File.ReadAllTextAsync(manifestPath, cancellationToken))?.AsObject()
            ?? throw new InvalidDataException("AutoCAD returned an invalid Xref dependency manifest.");
        var rootFullPath = Path.GetFullPath(root);
        var rootDirectory = Path.GetDirectoryName(rootFullPath)!;
        var files = new List<(string Source, string Logical, bool Root)> { (rootFullPath, Path.GetFileName(root), true) };
        var missing = new List<MissingReference>();
        var externalRoots = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var dependencyNode in manifest["dependencies"]?.AsArray() ?? [])
        {
            var dependency = dependencyNode?.AsObject();
            var raw = dependency?["path"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(raw)) continue;
            var source = Path.GetFullPath(Path.IsPathRooted(raw) ? raw : Path.Combine(rootDirectory, raw));
            var complete = dependency?["complete"]?.GetValue<bool>() == true && File.Exists(source);
            if (!complete)
            {
                missing.Add(new MissingReference(raw.Replace('\\', '/'), Path.GetFileName(root), "xref",
                    "Resolve or reload this Xref in AutoCAD, save the drawing, and try again."));
                continue;
            }
            var relative = Path.GetRelativePath(rootDirectory, source);
            string logical;
            if (relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal))
                logical = relative.Replace('\\', '/');
            else
            {
                var sourceDirectory = Path.GetDirectoryName(source)!;
                if (!externalRoots.TryGetValue(sourceDirectory, out var external))
                {
                    external = $"external{externalRoots.Count + 1}";
                    externalRoots[sourceDirectory] = external;
                }
                logical = $"{external}/{Path.GetFileName(source)}";
            }
            if (!files.Any(file => file.Source.Equals(source, StringComparison.OrdinalIgnoreCase)))
                files.Add((source, logical, false));
        }
        var staging = Path.Combine(output, "native-package");
        Directory.CreateDirectory(staging);
        foreach (var file in files)
        {
            var target = PackagePath(staging, file.Logical);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file.Source, target, true);
            File.SetAttributes(target, File.GetAttributes(target) & ~FileAttributes.ReadOnly);
        }
        if (files.Count > 1)
        {
            var mappingPath = Path.Combine(output, "xref-mapping.json");
            var mappings = files.Where(file => !file.Root).ToDictionary(
                file => Path.GetFullPath(file.Source),
                file => PackagePath(staging, file.Logical), StringComparer.OrdinalIgnoreCase);
            await File.WriteAllTextAsync(mappingPath, JsonSerializer.Serialize(mappings), cancellationToken);
            var rebaseScript = Path.Combine(output, "modelryx-rebase.scr");
            await File.WriteAllLinesAsync(rebaseScript,
            [
                "FILEDIA", "0", "SECURELOAD", "0", "_.NETLOAD", plugin.Replace('\\', '/'),
                "MODELRYXPREPROCESSREBASE", mappingPath.Replace('\\', '/'), "_.QSAVE", "_.QUIT", "_N",
            ], cancellationToken);
            await RunAsync(console,
                ["/i", PackagePath(staging, Path.GetFileName(root)), "/s", rebaseScript, "/l", "en-US"],
                cancellationToken);
            await ValidateAutoCadPackageAsync(console, plugin, staging, Path.GetFileName(root), output,
                cancellationToken);
        }
        var package = Path.Combine(output, "cad-package.zip");
        using (var archive = ZipFile.Open(package, ZipArchiveMode.Create))
            foreach (var file in files)
                archive.CreateEntryFromFile(PackagePath(staging, file.Logical), file.Logical, CompressionLevel.Optimal);
        var documentType = manifest["documentType"]?.GetValue<string>() ?? "PART";
        var metadataPath = Path.Combine(output, "part-metadata.json");
        var metadata = File.Exists(metadataPath)
            ? JsonNode.Parse(await File.ReadAllTextAsync(metadataPath, cancellationToken))?.AsObject()
            : null;
        var report = new DependencyReport(
            "1.0", "modelryx-autocad-dependencies-1",
            documentType, Path.GetFileName(root), Path.GetFileName(root),
            files.Select(file => new DependencyFile(file.Logical, file.Root,
                file.Root ? documentType : "SUPPORTING")).ToArray(),
            missing, [], true, metadata, Sha256(rootFullPath));
        PackageContractValidator.Validate(report, package);
        return new AnalysisOutput(report, package);
    }

    private static async Task ValidateAutoCadPackageAsync(
        string console, string plugin, string staging, string rootLogicalPath, string output,
        CancellationToken cancellationToken)
    {
        var validation = Path.Combine(output, "autocad-package-validation");
        Directory.CreateDirectory(validation);
        var script = Path.Combine(output, "modelryx-validate.scr");
        await File.WriteAllLinesAsync(script,
        [
            "FILEDIA", "0", "SECURELOAD", "0", "_.NETLOAD", plugin.Replace('\\', '/'),
            "MODELRYXPREPROCESSANALYZE", validation.Replace('\\', '/'), "_.QUIT", "_N",
        ], cancellationToken);
        await RunAsync(console,
            ["/i", PackagePath(staging, rootLogicalPath), "/s", script, "/l", "en-US"], cancellationToken);
        var manifestPath = Path.Combine(validation, "xref-dependencies.json");
        var manifest = JsonNode.Parse(await File.ReadAllTextAsync(manifestPath, cancellationToken))?.AsObject()
            ?? throw new InvalidDataException("AutoCAD returned invalid package-validation output.");
        foreach (var node in manifest["dependencies"]?.AsArray() ?? [])
        {
            var dependency = node?.AsObject();
            if (dependency?["complete"]?.GetValue<bool>() != true)
                throw new InvalidDataException("The staged AutoCAD package contains an unresolved Xref.");
            var raw = dependency?["path"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(raw)) continue;
            var resolved = Path.GetFullPath(Path.IsPathRooted(raw)
                ? raw : Path.Combine(Path.GetDirectoryName(PackagePath(staging, rootLogicalPath))!, raw));
            if (!IsWithin(resolved, staging))
                throw new InvalidDataException($"The staged AutoCAD package still resolves outside the package: '{raw}'.");
        }
    }

    private static string PackagePath(string staging, string logicalPath) => Path.GetFullPath(
        Path.Combine(staging, logicalPath.Replace('/', Path.DirectorySeparatorChar)));

    private static bool IsWithin(string file, string directory)
    {
        var relative = Path.GetRelativePath(directory, file);
        return relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar,
            StringComparison.Ordinal) && !Path.IsPathRooted(relative);
    }

    private static string AutoCadDiagnostic(string output)
    {
        var normalized = output.Replace("\0", "").Replace('\r', ' ').Replace('\n', ' ').Trim();
        if (string.IsNullOrWhiteSpace(normalized)) return "Reinstall the Local CAD Agent and try again.";
        const int maximum = 600;
        return normalized.Length <= maximum ? normalized : normalized[^maximum..];
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

    private static async Task StopOwnedInventorAsync(string outputDirectory)
    {
        var marker = Path.Combine(outputDirectory, "inventor-owned-process.pid");
        if (!File.Exists(marker)) return;
        try
        {
            if (!int.TryParse(await File.ReadAllTextAsync(marker), out var processId)) return;
            using var process = Process.GetProcessById(processId);
            if (process.ProcessName.Equals("Inventor", StringComparison.OrdinalIgnoreCase))
                await StopProcessAsync(process);
        }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException
            or Win32Exception or IOException or UnauthorizedAccessException) { }
        finally
        {
            try { File.Delete(marker); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }

    private static string RequiredFile(string environmentName, string message)
    {
        var value = Environment.GetEnvironmentVariable(environmentName);
        if (string.IsNullOrWhiteSpace(value) || !File.Exists(value))
            throw new InvalidOperationException(message);
        return Path.GetFullPath(value);
    }

    private static void RequireWindows(string application)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException(
                $"{application} analysis is supported only on Windows in this local CAD agent.");
    }

    private static string? FindFreeCadPython()
    {
        var candidates = OperatingSystem.IsWindows()
            ? new[]
            {
                @"C:\Program Files\FreeCAD 1.1\bin\python.exe",
                @"C:\Program Files\FreeCAD 1.0\bin\python.exe",
                @"C:\Program Files\FreeCAD 0.21\bin\python.exe",
            }
            : new[] { "/usr/lib/freecad/bin/python3", "/usr/bin/freecadcmd", "/usr/bin/FreeCADCmd" };
        return candidates.FirstOrDefault(File.Exists);
    }
}
