using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using Modelryx.LocalCad.Agent;

static void Assert(bool condition, string message)
{ if (!condition) throw new InvalidDataException("Assertion failed: " + message); }

// The test apphost also acts as a disposable CLI fixture. Actual NX is covered separately.
if (args.Length > 0)
{
    var validate = args[0] == "--validate-package";
    var package = args[1];
    var output = Path.GetDirectoryName(package)!;
    Assert(Environment.GetEnvironmentVariable("UGII_BASE_DIR") == Environment.GetEnvironmentVariable("MODELRYX_TEST_NX_DIRECTORY"),
        "NX installation is scoped to the child");
    Assert(Environment.GetEnvironmentVariable("MODELRYX_NXOPEN_DIR") ==
        Path.Combine(Environment.GetEnvironmentVariable("UGII_BASE_DIR")!, "NXBIN", "managed_core"), "managed_core configured");
    Assert(Environment.GetEnvironmentVariable("TEMP") == Path.Combine(output, "nx-diagnostics"), "diagnostics are isolated");
    File.AppendAllText(Path.Combine(output, "invocations.log"), validate ? "validate\n" : "discover\n");
    if (!validate)
    {
        Assert(args.Contains("--discover-references"), "agent requests native discovery");
        using var zip = ZipFile.Open(package, ZipArchiveMode.Create);
        zip.CreateEntryFromFile(args[0], "root.prt");
        using var writer = new StreamWriter(zip.CreateEntry("child.prt").Open());
        writer.Write("child CAD fixture");
        Console.WriteLine("{}");
    }
    else
    {
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            contract_version = "1.0", analyzer_version = "modelryx-nx-dependencies-1", document_type = "ASSEMBLY",
            root_file = "root.prt", root_logical_path = "root.prt", package,
            files = new[] { new { logical_path = "root.prt", is_root = true, document_type = "ASSEMBLY" },
                new { logical_path = "child.prt", is_root = false, document_type = "PART" } },
            references = new[] { new { parent_logical_path = "root.prt", child_logical_path = "child.prt", instance_name = "child:1" } },
            missing_references = Array.Empty<object>(),
            isolated_reopen_validated = Environment.GetEnvironmentVariable("MODELRYX_TEST_NX_INVALID") != "1"
        }));
    }
    return;
}

if (!OperatingSystem.IsWindows())
{
    try { await CadAnalyzer.AnalyzeAsync("root.prt", Path.GetTempPath(), CancellationToken.None); }
    catch (PlatformNotSupportedException) { Console.WriteLine("PASS: NX reports unsupported platform."); return; }
    throw new Exception("NX should require Windows.");
}
var directory = Path.Combine(Path.GetTempPath(), "modelryx-local-nx-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
var names = new[] { "MODELRYX_NX_PREPROCESSOR_EXE", "MODELRYX_NX_DIRECTORY", "MODELRYX_TEST_NX_DIRECTORY", "MODELRYX_TEST_NX_INVALID" };
var saved = names.ToDictionary(name => name, Environment.GetEnvironmentVariable);
var originalNxBase = Environment.GetEnvironmentVariable("UGII_BASE_DIR");
try
{
    var nx = Path.Combine(directory, "fake-NX2506");
    foreach (var relative in new[] { "NXBIN/managed_core/NXOpen.dll", "NXBIN/managed_core/NXOpen.UF.dll", "NXBIN/libufun.dll" })
    {
        var path = Path.Combine(nx, relative); Directory.CreateDirectory(Path.GetDirectoryName(path)!); File.WriteAllText(path, "runtime marker");
    }
    Environment.SetEnvironmentVariable("MODELRYX_NX_DIRECTORY", nx);
    Environment.SetEnvironmentVariable("MODELRYX_TEST_NX_DIRECTORY", nx);
    Environment.SetEnvironmentVariable("MODELRYX_NX_PREPROCESSOR_EXE", Path.Combine(AppContext.BaseDirectory, "Modelryx.LocalCad.Agent.Tests.exe"));
    var configuration = AgentConfiguration.Load();
    Assert(configuration.RequireNxDirectory() == nx, "configured NX installation detected");
    var root = Path.Combine(directory, "root.prt"); File.WriteAllText(root, "source CAD fixture");
    var hash = SHA256.HashData(File.ReadAllBytes(root));
    var output = Path.Combine(directory, "success"); Directory.CreateDirectory(output);
    var result = await CadAnalyzer.AnalyzeAsync(root, output, CancellationToken.None);
    Assert(result.Report.DocumentType == "ASSEMBLY" && result.Report.IsolatedReopenValidated && result.Report.Files.Count == 2,
        "NX neutral report integrates with the agent");
    Assert(result.Report.References!.Single().InstanceName == "child:1", "occurrence reference retained");
    Assert(result.Report.Files[1].DocumentType == "PART", "native child modeling files are not processing-only support");
    Assert(File.ReadAllLines(Path.Combine(output, "invocations.log")).SequenceEqual(new[] { "discover", "validate" }),
        "agent uses two independent native processes");
    Assert(SHA256.HashData(File.ReadAllBytes(root)).SequenceEqual(hash), "source untouched");
    Assert(Environment.GetEnvironmentVariable("UGII_BASE_DIR") == originalNxBase, "parent NX environment unchanged");
    Environment.SetEnvironmentVariable("MODELRYX_TEST_NX_INVALID", "1");
    var invalidOutput = Path.Combine(directory, "invalid"); Directory.CreateDirectory(invalidOutput);
    try
    {
        await CadAnalyzer.AnalyzeAsync(root, invalidOutput, CancellationToken.None);
        throw new Exception("Unvalidated NX package should fail.");
    }
    catch (InvalidDataException error) when (error.Message.Contains("reopens in isolation")) { }
    Console.WriteLine("All local CAD agent NX integration tests passed.");
}
finally
{
    foreach (var (name, value) in saved) Environment.SetEnvironmentVariable(name, value);
    Directory.Delete(directory, true);
}
