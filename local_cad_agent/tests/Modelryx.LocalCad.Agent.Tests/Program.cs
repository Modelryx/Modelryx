using System.IO.Compression;
using System.Text.Json;
using Modelryx.LocalCad.Agent;

static void Assert(bool condition, string message)
{ if (!condition) throw new InvalidDataException("Assertion failed: " + message); }

// The test executable doubles as an independently installed preprocessor.
if (args.Length == 2)
{
    var output = args[1];
    var package = Path.Combine(output, "cad-package.zip");
    using (var zip = ZipFile.Open(package, ZipArchiveMode.Create))
        zip.CreateEntryFromFile(args[0], "root.example");
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        contract_version = "1.0", analyzer_version = "example-1", document_type = "PART",
        root_file = "root.example", root_logical_path = "root.example", package,
        files = new[] { new { logical_path = "root.example", is_root = true, document_type = "PART" } },
        references = Array.Empty<object>(), missing_references = Array.Empty<object>(),
        isolated_reopen_validated = true,
    }));
    return;
}

var assemblyFormat = new WorkerFormat("assembly", "Assembly CAD", "Assembly worker", "1.0",
    [".assembly", ".part", ".drawing"]);
var drawingFormat = new WorkerFormat("drawing", "Drawing CAD", "Drawing worker", "1.0", [".drawing"]);
var filters = NativeFilePicker.BuildFilters([drawingFormat, assemblyFormat]);
Assert(filters.Select(filter => filter.Name).SequenceEqual(new[] { "Assembly worker", "Drawing worker" }),
    "only registered systems are shown, sorted by name");
Assert(NativeFilePicker.SelectFile("shared.DRAWING", filters[0]).Worker?.Id == "assembly",
    "selected system determines shared-extension routing");
Assert(NativeFilePicker.SelectFile("shared.drawing", filters[1]).Worker?.Id == "drawing",
    "shared extension preserves the selected worker");
Assert(filters.All(filter => filter.CadSystem != "freecad"), "FreeCAD requires registration");
var freecad = new WorkerFormat("external-freecad", "FreeCAD", "FreeCAD", "1.0", [".fcstd"]);
var registered = NativeFilePicker.BuildFilters([freecad, assemblyFormat, drawingFormat]);
Assert(registered.Count == 3 && NativeFilePicker.SelectFile("part.FCStd", registered.Single(filter => filter.CadSystem == "freecad")).Worker?.Id == "external-freecad",
    "registered FreeCAD uses the external preprocessor");
var advertisedFreecad = freecad with { WorkerName = "Custom native server", SourceExtensions = [".fcstd", ".fcnext"] };
var advertisedFilter = NativeFilePicker.BuildFilters([advertisedFreecad]).Single();
Assert(advertisedFilter.Label == "Custom native server (*.fcnext;*.fcstd)",
    "picker name and every extension come from advertised capabilities");
Assert(NativeFilePicker.SelectFile("part.fcnext", advertisedFilter).Worker?.Id == freecad.Id,
    "worker-provided additional extensions are selectable without client changes");
try
{
    NativeFilePicker.SelectFile("model.part", filters[1]);
    throw new Exception("Unsupported extension was accepted.");
}
catch (InvalidOperationException error) when (error.Message.Contains("Drawing worker file type")) { }
var duplicates = NativeFilePicker.BuildFilters([drawingFormat, drawingFormat with { Id = "other" }]);
try
{
    NativeFilePicker.SelectFile("shared.drawing", duplicates.Single(filter => filter.CadSystem == "drawingcad"));
    throw new Exception("Ambiguous registrations were accepted.");
}
catch (InvalidOperationException error) when (error.Message.Contains("More than one registered")) { }
try
{
    await CadAnalyzer.AnalyzeAsync("unregistered.example", Path.GetTempPath(), CancellationToken.None);
    throw new Exception("Unregistered format was accepted.");
}
catch (InvalidOperationException error) when (error.Message.Contains("external CAD worker")) { }

var directory = Path.Combine(Path.GetTempPath(), "modelryx-agent-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
var settingsPath = Path.Combine(AppContext.BaseDirectory, "settings.json");
var previousSettings = File.Exists(settingsPath) ? File.ReadAllBytes(settingsPath) : null;
try
{
    var executable = Path.Combine(AppContext.BaseDirectory,
        OperatingSystem.IsWindows() ? "Modelryx.LocalCad.Agent.Tests.exe" : "Modelryx.LocalCad.Agent.Tests");
    File.WriteAllText(settingsPath, JsonSerializer.Serialize(new
    {
        ExternalPreprocessors = new[] { new { Id = "example", CadSystem = "Example CAD", Version = "1.0",
            Executable = executable, Extensions = new[] { ".example" } } },
    }));
    var root = Path.Combine(directory, "root.example");
    File.WriteAllText(root, "native source");
    var output = Path.Combine(directory, "output");
    Directory.CreateDirectory(output);
    var worker = new WorkerFormat("example", "Example CAD", "Example worker", "1.0", [".example"]);
    var result = await CadAnalyzer.AnalyzeAsync(root, output, CancellationToken.None, worker);
    Assert(result.Report.IsolatedReopenValidated && result.Report.Files.Count == 1,
        "registered executable returns a validated neutral package");
    Assert(File.ReadAllText(root) == "native source", "source remains unchanged");
}
finally
{
    if (previousSettings is null) File.Delete(settingsPath);
    else File.WriteAllBytes(settingsPath, previousSettings);
    Directory.Delete(directory, true);
}
Console.WriteLine("All local CAD agent contract tests passed.");
