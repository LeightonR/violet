using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class VioletLauncher
{
    private const string AppUserModelId = "Violet.DesktopDictation";
    private const ushort VT_LPWSTR = 31;

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [STAThread]
    private static int Main(string[] args)
    {
        SetCurrentProcessExplicitAppUserModelID(AppUserModelId);

        if (args.Length == 2 && string.Equals(args[0], "--set-shortcut-app-id", StringComparison.OrdinalIgnoreCase))
        {
            return SetShortcutAppUserModelId(args[1]);
        }

        string mode = SelectMode(args);
        if (mode == null)
        {
            MessageBox.Show(
                "Violet can start in the background or open its control panel.",
                "Violet",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return 2;
        }

        string projectRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string pythonw = Path.Combine(projectRoot, ".venv", "Scripts", "pythonw.exe");
        if (!File.Exists(pythonw))
        {
            MessageBox.Show(
                "Violet needs setup before it can start. Run Setup-Violet.ps1 from the Violet folder.",
                "Violet setup needed",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return 1;
        }

        try
        {
            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = pythonw,
                Arguments = "-m violet " + mode,
                WorkingDirectory = projectRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            Process process = Process.Start(startInfo);
            return process == null ? 1 : 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "Violet could not start. " + error.Message,
                "Violet",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string SelectMode(string[] args)
    {
        if (args.Length == 0 || string.Equals(args[0], "--control-panel", StringComparison.OrdinalIgnoreCase))
        {
            return "--control-panel";
        }
        if (string.Equals(args[0], "--background", StringComparison.OrdinalIgnoreCase)
            || string.Equals(args[0], "--startup", StringComparison.OrdinalIgnoreCase))
        {
            return "--background";
        }
        return null;
    }

    private static int SetShortcutAppUserModelId(string shortcutPath)
    {
        if (!File.Exists(shortcutPath))
        {
            return 1;
        }

        try
        {
            object shellLink = new ShellLink();
            IPersistFile persistFile = (IPersistFile)shellLink;
            persistFile.Load(shortcutPath, 2); // STGM_READWRITE
            IPropertyStore propertyStore = (IPropertyStore)shellLink;
            PropertyKey key = new PropertyKey(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                5); // PKEY_AppUserModel_ID
            PropVariant value = PropVariant.FromString(AppUserModelId);
            try
            {
                propertyStore.SetValue(ref key, ref value);
                propertyStore.Commit();
                persistFile.Save(shortcutPath, true);
            }
            finally
            {
                value.Dispose();
            }
            return 0;
        }
        catch
        {
            return 1;
        }
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink
    {
    }

    [ComImport]
    [Guid("0000010B-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPersistFile
    {
        void GetClassID(out Guid classId);
        int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, bool remember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string fileName);
    }

    [ComImport]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        uint GetCount(out uint propertyCount);
        uint GetAt(uint propertyIndex, out PropertyKey key);
        uint GetValue(ref PropertyKey key, out PropVariant value);
        uint SetValue(ref PropertyKey key, ref PropVariant value);
        uint Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PropertyKey
    {
        public PropertyKey(Guid formatId, uint propertyId)
        {
            FormatId = formatId;
            PropertyId = propertyId;
        }

        public Guid FormatId;
        public uint PropertyId;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct PropVariant : IDisposable
    {
        [FieldOffset(0)]
        private ushort valueType;

        [FieldOffset(8)]
        private IntPtr pointerValue;

        public static PropVariant FromString(string value)
        {
            PropVariant result = new PropVariant();
            result.valueType = VT_LPWSTR;
            result.pointerValue = Marshal.StringToCoTaskMemUni(value);
            return result;
        }

        public void Dispose()
        {
            if (valueType == VT_LPWSTR && pointerValue != IntPtr.Zero)
            {
                Marshal.FreeCoTaskMem(pointerValue);
                pointerValue = IntPtr.Zero;
            }
        }
    }
}
