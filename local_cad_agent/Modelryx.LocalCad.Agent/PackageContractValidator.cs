using System.IO.Compression;

namespace Modelryx.LocalCad.Agent;

internal static class PackageContractValidator
{
    public static void Validate(DependencyReport report, string packagePath)
    {
        if (report.Evidence?.TryGetValue("managed_bom_supported", out var supported) == true &&
            supported.ValueKind == System.Text.Json.JsonValueKind.False)
            throw new InvalidDataException(
                "This CAD package requires configuration or component identity mapping before managed BOM staging. Native occurrence evidence is retained in the analyzer output.");
        if (!report.IsolatedReopenValidated)
            throw new InvalidDataException(
                "The CAD adapter did not confirm that the packaged root reopens in isolation.");
        if (string.IsNullOrWhiteSpace(report.RootLogicalPath))
            throw new InvalidDataException("The CAD adapter did not identify a package root.");
        var declared = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in report.Files)
        {
            var logical = Normalize(file.LogicalPath);
            if (!declared.Add(logical))
                throw new InvalidDataException($"The CAD adapter declared duplicate path '{logical}'.");
        }
        var root = Normalize(report.RootLogicalPath);
        if (!declared.Contains(root) || report.Files.Count(file => file.IsRoot) != 1)
            throw new InvalidDataException("The CAD package must declare exactly one existing root document.");

        using var archive = ZipFile.OpenRead(packagePath);
        var packaged = archive.Entries
            .Where(entry => !string.IsNullOrEmpty(entry.Name))
            .Select(entry => Normalize(entry.FullName))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var logical in declared)
            if (!packaged.Contains(logical))
                throw new InvalidDataException($"The CAD package is missing declared file '{logical}'.");

        foreach (var reference in report.References ?? [])
        {
            var parent = Normalize(reference.ParentLogicalPath);
            var child = Normalize(reference.ChildLogicalPath);
            if (!declared.Contains(parent) || !declared.Contains(child))
                throw new InvalidDataException(
                    $"The dependency graph contains an edge outside the package: '{parent}' -> '{child}'.");
        }
    }

    private static string Normalize(string value)
    {
        var normalized = value.Replace('\\', '/').TrimStart('/');
        if (string.IsNullOrWhiteSpace(normalized) || Path.IsPathRooted(value) ||
            normalized.Split('/').Any(part => part is "" or "." or ".."))
            throw new InvalidDataException($"Unsafe CAD package path '{value}'.");
        return normalized;
    }
}
