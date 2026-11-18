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
    [property: JsonPropertyName("instance_name")] string? InstanceName = null,
    [property: JsonPropertyName("occurrence_id")] string? OccurrenceId = null,
    [property: JsonPropertyName("occurrence_key")] string? OccurrenceKey = null,
    [property: JsonPropertyName("transform")] double[]? Transform = null,
    [property: JsonPropertyName("is_visible")] bool? IsVisible = null,
    [property: JsonPropertyName("is_suppressed")] bool? IsSuppressed = null,
    [property: JsonPropertyName("source_reference")] string? SourceReference = null,
    [property: JsonPropertyName("reference_designator")] string? ReferenceDesignator = null,
    [property: JsonPropertyName("properties")] JsonObject? Properties = null);

public sealed record DependencyReport(
    [property: JsonPropertyName("contract_version")] string ContractVersion,
    [property: JsonPropertyName("analyzer_version")] string AnalyzerVersion,
    [property: JsonPropertyName("document_type")] string DocumentType,
    [property: JsonPropertyName("root_file")] string RootFile,
    [property: JsonPropertyName("root_logical_path")] string RootLogicalPath,
    [property: JsonPropertyName("files")] IReadOnlyList<DependencyFile> Files,
    [property: JsonPropertyName("missing_references")] IReadOnlyList<MissingReference> MissingReferences,
    [property: JsonPropertyName("references")] IReadOnlyList<DependencyReference>? References = null,
    [property: JsonPropertyName("isolated_reopen_validated")] bool IsolatedReopenValidated = false);

public sealed record AnalysisOutput(DependencyReport Report, string PackagePath);
public sealed record SelectionJob(
    Guid Id, DateTimeOffset CreatedAt, string Workspace, CancellationTokenSource Cancellation,
    Task<AnalysisOutput> Analysis, WorkerFormat? Worker = null);
