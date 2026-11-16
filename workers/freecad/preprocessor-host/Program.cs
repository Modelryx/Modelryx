using System.Diagnostics;
if (args.Length != 2) { Console.Error.WriteLine("Usage: Modelryx.FreeCAD.Preprocessor <source.FCStd> <output-directory>"); return 2; }
try
{
    var python = Environment.GetEnvironmentVariable("MODELRYX_FREECAD_CMD");
    if (string.IsNullOrWhiteSpace(python))
        python = new[] { "FreeCAD 1.1", "FreeCAD 1.0" }.Select(version => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), version, "bin", "python.exe"))
            .FirstOrDefault(File.Exists);
    if (string.IsNullOrWhiteSpace(python) || !File.Exists(python))
        throw new FileNotFoundException("Set MODELRYX_FREECAD_CMD to a Python executable that can import FreeCAD.");
    var script = Path.Combine(AppContext.BaseDirectory, "local_preprocessor.py");
    if (!File.Exists(script)) throw new FileNotFoundException("Install the complete FreeCAD preprocessor distribution.");
    var start = new ProcessStartInfo(python) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
    start.ArgumentList.Add("-u");
    start.ArgumentList.Add(script);
    foreach (var argument in args) start.ArgumentList.Add(Path.GetFullPath(argument));
    using var process = Process.Start(start) ?? throw new InvalidOperationException("Could not start FreeCAD Python.");
    var stdout = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
    var stderr = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
    using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
    try { await process.WaitForExitAsync(timeout.Token); }
    catch (OperationCanceledException) { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); throw new TimeoutException("FreeCAD preprocessing timed out."); }
    await Task.WhenAll(stdout, stderr);
    return process.ExitCode;
}
catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
