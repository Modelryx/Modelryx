using System.Text.Json;

namespace Modelryx.LocalCad.Agent;

internal sealed record AgentSettings(
    ExternalPreprocessor[]? ExternalPreprocessors = null,
    string[]? AllowedOrigins = null);

internal sealed record ExternalPreprocessor(
    string Id, string CadSystem, string Version, string Executable, string[] Extensions);

public sealed record WorkerFormat(string Id, string CadSystem, string WorkerName,
    string WorkerVersion, string[] SourceExtensions);

internal sealed class AgentConfiguration
{
    private readonly AgentSettings _settings;

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
