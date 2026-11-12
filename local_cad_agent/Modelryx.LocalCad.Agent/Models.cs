using System.Text.Json.Serialization;
using System.Text.Json.Nodes;

namespace Modelryx.LocalCad.Agent;

public sealed record DependencyFile(
    [property: JsonPropertyName("logical_path")] string LogicalPath,
    [property: JsonPropertyName("is_root")] bool IsRoot,
    [property: JsonPropertyName("document_type")] string? DocumentType = null);

public sealed record MissingReference(
    [property: JsonPropertyName("reference")] string? Reference,
    [property: JsonPropertyName("referenced_by")] string ReferencedBy,
    [property: JsonPropertyName("kind")] string Kind = "document-reference",
    [property: JsonPropertyName("suggestion")] string? Suggestion = null);

public sealed record DependencyReference(
    [property: JsonPropertyName("parent_logical_path")] string ParentLogicalPath,
    [property: JsonPropertyName("child_logical_path")] string ChildLogicalPath,
    [property: JsonPropertyName("instance_name")] string? InstanceName = null);

public sealed record DependencyReport(
    [property: JsonPropertyName("contract_version")] string ContractVersion,
    [property: JsonPropertyName("analyzer_version")] string AnalyzerVersion,
    [property: JsonPropertyName("document_type")] string DocumentType,
    [property: JsonPropertyName("root_file")] string RootFile,
    [property: JsonPropertyName("root_logical_path")] string RootLogicalPath,
    [property: JsonPropertyName("files")] IReadOnlyList<DependencyFile> Files,
    [property: JsonPropertyName("missing_references")] IReadOnlyList<MissingReference> MissingReferences,
    [property: JsonPropertyName("references")] IReadOnlyList<DependencyReference>? References = null,
    [property: JsonPropertyName("isolated_reopen_validated")] bool IsolatedReopenValidated = false,
    [property: JsonPropertyName("part_metadata")] JsonObject? PartMetadata = null,
    [property: JsonPropertyName("metadata_source_sha256")] string? MetadataSourceSha256 = null);

public sealed record AnalysisOutput(DependencyReport Report, string PackagePath);
public sealed record SelectionJob(
    Guid Id, DateTimeOffset CreatedAt, string Workspace, CancellationTokenSource Cancellation,
    Task<AnalysisOutput> Analysis);
