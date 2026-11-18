using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Modelryx.LocalCad.Agent;

internal sealed record CadFileSelection(string Path, WorkerFormat? Worker);
internal sealed record CadFileFilter(string CadSystem, string Name, string[] Extensions, WorkerFormat[] Workers)
{
    public string Patterns => string.Join(';', Extensions.Select(extension => "*" + extension));
    public string Label => $"{Name} ({Patterns})";
}

internal static class NativeFilePicker
{
    internal static IReadOnlyList<CadFileFilter> BuildFilters(IReadOnlyList<WorkerFormat> formats)
    {
        var filters = formats.GroupBy(worker => System.Text.RegularExpressions.Regex.Replace(
                worker.CadSystem.ToLowerInvariant(), "[^a-z0-9]", ""))
            .Select(group => new CadFileFilter(group.Key, group.First().WorkerName,
                group.SelectMany(worker => worker.SourceExtensions)
                .Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(extension => extension, StringComparer.OrdinalIgnoreCase)
                .ToArray(), group.ToArray())).ToList();
        if (filters.Count == 0)
            throw new InvalidOperationException("Register an enabled external CAD worker before opening a CAD file.");
        return filters.OrderBy(filter => filter.Name, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    internal static CadFileSelection SelectFile(string path, CadFileFilter filter)
    {
        var extension = System.IO.Path.GetExtension(path);
        if (!filter.Extensions.Contains(extension, StringComparer.OrdinalIgnoreCase))
            throw new InvalidOperationException($"Select a file supported by the {filter.Name} file type.");
        var matches = filter.Workers.Where(worker => worker.SourceExtensions.Contains(
            extension, StringComparer.OrdinalIgnoreCase)).ToArray();
        if (matches.Length > 1)
            throw new InvalidOperationException($"More than one registered {filter.Name} worker supports this extension. Disable the conflicting registration before selecting this file.");
        return new CadFileSelection(path, matches.SingleOrDefault());
    }

    public static CadFileSelection? PickCadFile(IReadOnlyList<WorkerFormat> formats) => OperatingSystem.IsWindows()
        ? WindowsPicker.Pick(formats)
        : LinuxPicker.Pick(formats);

    public static Task<CadFileSelection?> PickCadFileAsync(IReadOnlyList<WorkerFormat> formats)
    {
        if (!OperatingSystem.IsWindows()) return Task.FromResult(PickCadFile(formats));
        var completion = new TaskCompletionSource<CadFileSelection?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            try { completion.SetResult(WindowsPicker.Pick(formats)); }
            catch (Exception error) { completion.SetException(error); }
        })
        {
            IsBackground = true,
            Name = "Modelryx native CAD file picker",
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        return completion.Task;
    }

    private static class LinuxPicker
    {
        public static CadFileSelection? Pick(IReadOnlyList<WorkerFormat> formats)
        {
            var filters = BuildFilters(formats);
            if (FindOnPath("zenity") is { } zenity)
            {
                // Zenity's file selection output omits the active filter. Choose the system first
                // so a shared extension cannot silently select the wrong native adapter.
                var name = filters.Count == 1 ? filters[0].Label : Run(zenity,
                    new[] { "--list", "--title=Open CAD File", "--text=Select CAD system", "--column=CAD system" }
                        .Concat(filters.Select(filter => filter.Label)));
                if (name is null) return null;
                var filter = filters.Single(filter => filter.Label == name);
                var path = Run(zenity,
                    ["--file-selection", "--title=Open CAD File", "--modal",
                     $"--file-filter={filter.Name} | {filter.Patterns.Replace(';', ' ')}"]);
                return path is null ? null : SelectFile(path, filter);
            }
            if (FindOnPath("kdialog") is { } kdialog)
            {
                var choice = filters.Count == 1 ? "0" : Run(kdialog,
                    new[] { "--title", "Open CAD File", "--menu", "Select CAD system" }
                        .Concat(filters.SelectMany((filter, index) => new[] { index.ToString(), filter.Label })));
                if (choice is null) return null;
                if (!int.TryParse(choice, out var index) || index < 0 || index >= filters.Count)
                    throw new InvalidDataException("The CAD system selection is invalid.");
                var filter = filters[index];
                var path = Run(kdialog,
                    ["--getopenfilename", Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                     $"{filter.Patterns.Replace(';', ' ')}|{filter.Name}", "--title", "Open CAD File"]);
                return path is null ? null : SelectFile(path, filter);
            }
            throw new InvalidOperationException(
                "No supported Linux file picker was found. Install zenity or kdialog.");
        }

        private static string? Run(string executable, IEnumerable<string> arguments)
        {
            var start = new ProcessStartInfo(executable)
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            foreach (var argument in arguments) start.ArgumentList.Add(argument);
            using var process = Process.Start(start)
                ?? throw new InvalidOperationException($"Could not start {Path.GetFileName(executable)}.");
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode == 1) return null;
            if (process.ExitCode != 0)
                throw new InvalidOperationException(process.StandardError.ReadToEnd().Trim());
            var path = output.Trim();
            return path.Length == 0 ? null : path;
        }

        private static string? FindOnPath(string name)
        {
            foreach (var directory in (Environment.GetEnvironmentVariable("PATH") ?? "")
                         .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
            {
                var candidate = Path.Combine(directory, name);
                if (File.Exists(candidate)) return candidate;
            }
            return null;
        }
    }

    private static class WindowsPicker
    {
        private const uint WsExTopmost = 0x00000008;
        private const uint WsExToolWindow = 0x00000080;
        private const uint FosForceFileSystem = 0x00000040;
        private const uint FosPathMustExist = 0x00000800;
        private const uint FosFileMustExist = 0x00001000;
        private const uint SigDnFileSystemPath = 0x80058000;
        private const int Cancelled = unchecked((int)0x800704C7);

        public static CadFileSelection? Pick(IReadOnlyList<WorkerFormat> formats)
        {
            var owner = CreateWindowExW(WsExTopmost | WsExToolWindow, "STATIC", "Modelryx",
                0, 0, 0, 1, 1, nint.Zero, nint.Zero, GetModuleHandleW(null), nint.Zero);
            try
            {
                SetWindowPos(owner, new nint(-1), 0, 0, 1, 1, 0x0010 | 0x0002 | 0x0001);
                SetForegroundWindow(owner);
                var dialog = (IFileOpenDialog)new FileOpenDialog();
                dialog.SetTitle("Open CAD File");
                dialog.SetOptions(FosForceFileSystem | FosPathMustExist | FosFileMustExist);
                var filters = BuildFilters(formats);
                var specs = filters.Select(filter => new FilterSpec(filter.Label, filter.Patterns)).ToArray();
                dialog.SetFileTypes((uint)specs.Length, specs);
                dialog.SetFileTypeIndex(1);
                var result = dialog.Show(owner);
                if (result == Cancelled) return null;
                Marshal.ThrowExceptionForHR(result);
                dialog.GetFileTypeIndex(out var selectedIndex);
                if (selectedIndex < 1 || selectedIndex > filters.Count)
                    throw new InvalidDataException("The selected CAD file type is invalid.");
                dialog.GetResult(out var item);
                item.GetDisplayName(SigDnFileSystemPath, out var pointer);
                try
                {
                    var path = Marshal.PtrToStringUni(pointer);
                    return path is null ? null : SelectFile(path, filters[(int)selectedIndex - 1]);
                }
                finally { Marshal.FreeCoTaskMem(pointer); }
            }
            finally
            {
                if (owner != nint.Zero) DestroyWindow(owner);
            }
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private readonly struct FilterSpec(string name, string spec)
        {
            [MarshalAs(UnmanagedType.LPWStr)] public readonly string Name = name;
            [MarshalAs(UnmanagedType.LPWStr)] public readonly string Spec = spec;
        }

        [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
        private class FileOpenDialog { }

        [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem
        {
            void BindToHandler(); void GetParent(); void GetDisplayName(uint sigdnName, out nint name);
        }

        [ComImport, Guid("D57C7288-D4AD-4768-BE02-9D969532D960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileOpenDialog
        {
            [PreserveSig] int Show(nint owner);
            void SetFileTypes(uint count, [MarshalAs(UnmanagedType.LPArray)] FilterSpec[] filters);
            void SetFileTypeIndex(uint index); void GetFileTypeIndex(out uint index);
            void Advise(); void Unadvise(); void SetOptions(uint options); void GetOptions(out uint options);
            void SetDefaultFolder(); void SetFolder(); void GetFolder(); void GetCurrentSelection();
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name); void GetFileName();
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
            void SetOkButtonLabel(); void SetFileNameLabel(); void GetResult(out IShellItem item);
            void AddPlace(); void SetDefaultExtension(); void Close(); void SetClientGuid();
            void ClearClientData(); void SetFilter(); void GetResults(); void GetSelectedItems();
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern nint CreateWindowExW(uint exStyle, string className, string windowName,
            uint style, int x, int y, int width, int height, nint parent, nint menu, nint instance, nint parameter);
        [DllImport("user32.dll")] private static extern bool DestroyWindow(nint window);
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(nint window);
        [DllImport("user32.dll")] private static extern bool SetWindowPos(nint window, nint insertAfter,
            int x, int y, int width, int height, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern nint GetModuleHandleW(string? moduleName);
    }

}
