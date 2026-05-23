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
            string runtimeArgs = "";
            string runtime = FindPythonRuntime(root, out runtimeArgs);

            if (!File.Exists(script) || string.IsNullOrWhiteSpace(runtime))
            {
                StartLegacy(root);
                return;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = runtime;
                psi.Arguments = runtimeArgs + Quote(script) + BuildForwardArgs(args);
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

        private static string FindPythonRuntime(string root, out string runtimeArgs)
        {
            runtimeArgs = "";
            string pythonw = Path.Combine(root, "python", "pythonw.exe");
            if (File.Exists(pythonw))
            {
                return pythonw;
            }
            string python = Path.Combine(root, "python", "python.exe");
            if (File.Exists(python))
            {
                return python;
            }

            string found = FindOnPath("pythonw.exe");
            if (!string.IsNullOrWhiteSpace(found))
            {
                return found;
            }
            found = FindOnPath("python.exe");
            if (!string.IsNullOrWhiteSpace(found))
            {
                return found;
            }
            found = FindOnPath("pyw.exe");
            if (!string.IsNullOrWhiteSpace(found))
            {
                runtimeArgs = "-3 ";
                return found;
            }
            found = FindOnPath("py.exe");
            if (!string.IsNullOrWhiteSpace(found))
            {
                runtimeArgs = "-3 ";
                return found;
            }
            return "";
        }

        private static string FindOnPath(string fileName)
        {
            string pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string rawDir in pathValue.Split(Path.PathSeparator))
            {
                string dir = (rawDir ?? "").Trim().Trim('"');
                if (string.IsNullOrWhiteSpace(dir))
                {
                    continue;
                }
                try
                {
                    string candidate = Path.Combine(dir, fileName);
                    if (File.Exists(candidate) && !IsWindowsAppsAlias(candidate))
                    {
                        return candidate;
                    }
                }
                catch
                {
                }
            }
            return "";
        }

        private static bool IsWindowsAppsAlias(string path)
        {
            string value = (path ?? "").ToLowerInvariant();
            return value.Contains("\\microsoft\\windowsapps\\");
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
