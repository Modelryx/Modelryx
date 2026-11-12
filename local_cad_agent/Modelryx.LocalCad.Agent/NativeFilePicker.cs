using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Modelryx.LocalCad.Agent;

internal static class NativeFilePicker
{
    public static string? PickCadFile() => OperatingSystem.IsWindows()
        ? WindowsPicker.Pick()
        : LinuxPicker.Pick();

    public static Task<string?> PickCadFileAsync()
    {
        if (!OperatingSystem.IsWindows()) return Task.FromResult(PickCadFile());
        var completion = new TaskCompletionSource<string?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            try { completion.SetResult(WindowsPicker.Pick()); }
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
        public static string? Pick()
        {
            if (FindOnPath("zenity") is { } zenity)
                return Run(zenity,
                    ["--file-selection", "--title=Open CAD File", "--modal",
                     "--file-filter=CAD files | *.FCStd *.fcstd *.dwg *.dxf *.iam *.ipt *.idw *.ipn *.prt"]);
            if (FindOnPath("kdialog") is { } kdialog)
                return Run(kdialog,
                    ["--getopenfilename", Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                     "CAD files (*.FCStd *.fcstd *.dwg *.dxf *.iam *.ipt *.idw *.ipn *.prt)", "--title", "Open CAD File"]);
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

        public static string? Pick()
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
                var filters = new[]
                {
                    new FilterSpec("CAD files", "*.FCStd;*.fcstd;*.dwg;*.dxf;*.iam;*.ipt;*.idw;*.ipn;*.prt"),
                    new FilterSpec("All files", "*.*"),
                };
                dialog.SetFileTypes((uint)filters.Length, filters);
                var result = dialog.Show(owner);
                if (result == Cancelled) return null;
                Marshal.ThrowExceptionForHR(result);
                dialog.GetResult(out var item);
                item.GetDisplayName(SigDnFileSystemPath, out var pointer);
                try { return Marshal.PtrToStringUni(pointer); }
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
