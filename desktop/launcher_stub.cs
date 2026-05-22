using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace LobsterDesktopLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string script = Path.Combine(root, "desktop", "launcher.py");
            string pythonw = Path.Combine(root, "python", "pythonw.exe");
            string python = Path.Combine(root, "python", "python.exe");
            string runtime = File.Exists(pythonw) ? pythonw : (File.Exists(python) ? python : "");

            if (!File.Exists(script) || string.IsNullOrWhiteSpace(runtime))
            {
                StartLegacy(root);
                return;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = runtime;
                psi.Arguments = Quote(script) + BuildForwardArgs(args);
                psi.WorkingDirectory = root;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.EnvironmentVariables["PYTHONPATH"] = root;
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                try
                {
                    File.AppendAllText(Path.Combine(root, "desktop_launcher.log"), "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] lightweight launcher failed: " + ex.Message + Environment.NewLine, Encoding.UTF8);
                }
                catch
                {
                }
                StartLegacy(root);
            }
        }

        private static void StartLegacy(string root)
        {
            string startBat = Path.Combine(root, "start.bat");
            if (!File.Exists(startBat))
            {
                return;
            }
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = startBat;
                psi.WorkingDirectory = root;
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch
            {
            }
        }

        private static string BuildForwardArgs(string[] args)
        {
            if (args == null || args.Length == 0)
            {
                return "";
            }
            StringBuilder sb = new StringBuilder();
            foreach (string arg in args)
            {
                sb.Append(' ');
                sb.Append(Quote(arg ?? ""));
            }
            return sb.ToString();
        }

        private static string Quote(string value)
        {
            return "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }
    }
}
