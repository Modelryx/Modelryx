using System.Text.Json;

namespace Modelryx.LocalCad.Agent;

internal sealed record AgentSettings(
    string? FreeCadCommand = null,
    string? FreeCadPreprocessorScript = null,
    string? FreeCadConverterScript = null,
    ExternalPreprocessor[]? ExternalPreprocessors = null,
    string[]? AllowedOrigins = null);

internal sealed record ExternalPreprocessor(
    string Id, string CadSystem, string Version, string Executable, string[] Extensions);

public sealed record WorkerFormat(string Id, string CadSystem, string WorkerName,
    string WorkerVersion, string[] SourceExtensions);

internal sealed class AgentConfiguration
{
    private readonly AgentSettings _settings;
    private readonly string _applicationDirectory = AppContext.BaseDirectory;

    private AgentConfiguration(AgentSettings settings) => _settings = settings;

    public static AgentConfiguration Load()
    {
        foreach (var path in ConfigurationPaths())
        {
            if (!File.Exists(path)) continue;
            try
            {
                var settings = JsonSerializer.Deserialize<AgentSettings>(File.ReadAllText(path),
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
                if (settings is not null) return new AgentConfiguration(settings);
            }
            catch (JsonException exception)
            {
                throw new InvalidDataException($"Invalid local CAD agent configuration: {path}", exception);
            }
        }
        return new AgentConfiguration(new AgentSettings());
    }

    public string RequireFreeCadPreprocessor() => RequireFile(
        "MODELRYX_FREECAD_PREPROCESSOR", _settings.FreeCadPreprocessorScript,
        Candidate("preprocessors", "freecad", "modelryx_freecad_preprocessor.py"),
        Candidate("workers", "freecad", "preprocessor", "modelryx_freecad_preprocessor.py"),
        Candidate("freecad-worker", "preprocessor", "modelryx_freecad_preprocessor.py"));

    public string RequireFreeCadConverter() => RequireFile(
        "MODELRYX_FREECAD_CONVERTER", _settings.FreeCadConverterScript,
        Candidate("preprocessors", "freecad", "fcstd_to_modelryx_glb.py"),
        Candidate("workers", "freecad", "converter", "fcstd_to_modelryx_glb.py"),
        Candidate("freecad-worker", "converter", "fcstd_to_modelryx_glb.py"));

    public string? FindFreeCadCommand() => FirstExistingFile(
        Environment.GetEnvironmentVariable("MODELRYX_FREECAD_CMD"),
        _settings.FreeCadCommand,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 1.1\bin\python.exe" : null,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 1.0\bin\python.exe" : null,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 0.21\bin\python.exe" : null,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe" : null,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe" : null,
        OperatingSystem.IsWindows() ? @"C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe" : null,
        "/usr/bin/freecadcmd", "/usr/local/bin/freecadcmd", "/usr/bin/FreeCADCmd");

    public IEnumerable<string> AllowedOrigins => _settings.AllowedOrigins ?? [];

    public ExternalPreprocessor? FindExternal(string id, string system, string extension)
    {
        var matches = (_settings.ExternalPreprocessors ?? [])
            .Where(worker => string.Equals(worker.Id, id, StringComparison.OrdinalIgnoreCase)
                && string.Equals(worker.CadSystem, system, StringComparison.OrdinalIgnoreCase)
                && (worker.Extensions ?? []).Contains(extension, StringComparer.OrdinalIgnoreCase))
            .ToArray();
        if (matches.Length > 1) throw new InvalidDataException("Duplicate local preprocessor registration.");
        return matches.SingleOrDefault();
    }

    public string RequireExternalExecutable(ExternalPreprocessor worker) =>
        FirstExistingFile(worker.Executable)
        ?? throw new InvalidOperationException($"Local preprocessor '{worker.Id}' is not installed. Install it and try again.");

    private string RequireFile(string environmentName, string? configured, params string?[] candidates) =>
        FirstExistingFile(new[] { Environment.GetEnvironmentVariable(environmentName), configured }.Concat(candidates))
        ?? throw new InvalidOperationException(
            $"The required CAD adapter was not found. Configure {environmentName} in settings.json or reinstall the local CAD agent.");

    private string Candidate(params string[] parts) => Path.GetFullPath(Path.Combine([_applicationDirectory, .. parts]));

    private static string? FirstExistingFile(params string?[] candidates) => FirstExistingFile(candidates.AsEnumerable());

    private static string? FirstExistingFile(IEnumerable<string?> candidates) => candidates
        .Where(path => !string.IsNullOrWhiteSpace(path))
        .Select(path => Path.GetFullPath(Environment.ExpandEnvironmentVariables(path!)))
        .FirstOrDefault(File.Exists);

    private static IEnumerable<string> ConfigurationPaths()
    {
        yield return Path.Combine(AppContext.BaseDirectory, "settings.json");
        var root = OperatingSystem.IsWindows()
            ? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
            : Environment.GetEnvironmentVariable("XDG_CONFIG_HOME")
                ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config");
        yield return Path.Combine(root, "Modelryx", "LocalCadAgent", "settings.json");
    }
}
