using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace LobsterOemConfigurator
{
    internal sealed class ConfigureResult
    {
        public bool Ok;
        public string Message = "";
        public string BrandName = "";
        public string LauncherPath = "";
    }

    internal sealed class ConfigForm : Form
    {
        private readonly TextBox codeInput = new TextBox();
        private readonly Button configureButton = new Button();
        private readonly Label statusLabel = new Label();
        private readonly BackgroundWorker worker = new BackgroundWorker();
        private readonly string root;

        public ConfigForm(string rootPath)
        {
            root = rootPath;
            Text = "AI智能体 OEM 配置";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            ClientSize = new Size(470, 276);
            BackColor = Color.White;
            Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);

            Label title = new Label();
            title.Text = "配置 OEM 客户端";
            title.Font = new Font("Microsoft YaHei UI", 18F, FontStyle.Regular, GraphicsUnit.Point);
            title.ForeColor = Color.FromArgb(24, 37, 54);
            title.SetBounds(36, 28, 390, 42);
            Controls.Add(title);

            Label hint = new Label();
            hint.Text = "输入工厂提供的数字编号，系统将下载品牌资源和启动程序。";
            hint.ForeColor = Color.FromArgb(92, 105, 122);
            hint.SetBounds(38, 76, 395, 28);
            Controls.Add(hint);

            codeInput.SetBounds(38, 116, 270, 38);
            codeInput.Font = new Font("Segoe UI", 15F, FontStyle.Regular, GraphicsUnit.Point);
            codeInput.MaxLength = 12;
            codeInput.TextAlign = HorizontalAlignment.Center;
            codeInput.KeyPress += delegate(object sender, KeyPressEventArgs e) {
                if (!char.IsControl(e.KeyChar) && !char.IsDigit(e.KeyChar)) e.Handled = true;
            };
            Controls.Add(codeInput);

            configureButton.Text = "下载并配置";
            configureButton.FlatStyle = FlatStyle.Flat;
            configureButton.FlatAppearance.BorderSize = 0;
            configureButton.BackColor = Color.FromArgb(35, 101, 232);
            configureButton.ForeColor = Color.White;
            configureButton.Cursor = Cursors.Hand;
            configureButton.SetBounds(320, 116, 112, 38);
            configureButton.Click += StartConfigure;
            Controls.Add(configureButton);

            statusLabel.Text = "配置完成后会自动启动品牌客户端。";
            statusLabel.ForeColor = Color.FromArgb(105, 117, 133);
            statusLabel.SetBounds(38, 176, 394, 54);
            Controls.Add(statusLabel);

            AcceptButton = configureButton;
            worker.DoWork += WorkerDoWork;
            worker.RunWorkerCompleted += WorkerCompleted;
        }

        private void StartConfigure(object sender, EventArgs e)
        {
            string code = (codeInput.Text ?? "").Trim();
            if (code.Length < 4)
            {
                statusLabel.ForeColor = Color.FromArgb(190, 45, 45);
                statusLabel.Text = "请输入 4 到 12 位数字 OEM 编号。";
                codeInput.Focus();
                return;
            }
            configureButton.Enabled = false;
            codeInput.Enabled = false;
            statusLabel.ForeColor = Color.FromArgb(35, 101, 232);
            statusLabel.Text = "正在校验编号并下载品牌资源，请稍候...";
            worker.RunWorkerAsync(code);
        }

        private void WorkerDoWork(object sender, DoWorkEventArgs e)
        {
            e.Result = RunConfigurator((string)e.Argument);
        }

        private void WorkerCompleted(object sender, RunWorkerCompletedEventArgs e)
        {
            configureButton.Enabled = true;
            codeInput.Enabled = true;
            if (e.Error != null)
            {
                statusLabel.ForeColor = Color.FromArgb(190, 45, 45);
                statusLabel.Text = e.Error.Message;
                return;
            }
            ConfigureResult result = e.Result as ConfigureResult;
            if (result == null || !result.Ok)
            {
                statusLabel.ForeColor = Color.FromArgb(190, 45, 45);
                statusLabel.Text = result == null ? "配置失败" : result.Message;
                return;
            }
            statusLabel.ForeColor = Color.FromArgb(23, 137, 82);
            statusLabel.Text = result.BrandName + " 配置完成，正在启动...";
            try
            {
                string launcherPath = Path.GetFullPath((result.LauncherPath ?? "").Trim().Trim('"'));
                string normalizedRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                if (!launcherPath.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase) || !File.Exists(launcherPath))
                {
                    throw new FileNotFoundException("品牌启动程序不存在", launcherPath);
                }
                ProcessStartInfo start = new ProcessStartInfo();
                start.FileName = launcherPath;
                start.WorkingDirectory = root;
                start.UseShellExecute = true;
                Process.Start(start);
                Close();
            }
            catch (Exception ex)
            {
                statusLabel.ForeColor = Color.FromArgb(190, 45, 45);
                statusLabel.Text = "配置完成，但启动失败：" + ex.Message;
            }
        }

        private ConfigureResult RunConfigurator(string code)
        {
            ConfigureResult result = new ConfigureResult();
            string script = Path.Combine(root, "desktop", "oem_configurator.py");
            string runtimeArgs;
            string runtime = FindPythonRuntime(root, out runtimeArgs);
            if (!File.Exists(script) || string.IsNullOrWhiteSpace(runtime))
            {
                result.Message = "客户端目录不完整，找不到配置程序或 Python 运行时。";
                return result;
            }
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = runtime;
                psi.Arguments = runtimeArgs + Quote(script) + " --code " + Quote(code) + " --root " + Quote(root);
                psi.WorkingDirectory = root;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["PYTHONPATH"] = root;
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                using (Process process = Process.Start(psi))
                {
                    string stdout = process.StandardOutput.ReadToEnd();
                    string stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    string[] lines = stdout.Replace("\r", "").Split(new char[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
                    string last = lines.Length > 0 ? lines[lines.Length - 1] : "";
                    string[] fields = last.Split(new char[] { '\t' }, 3);
                    if (process.ExitCode == 0 && fields.Length == 3 && fields[0] == "OK64")
                    {
                        result.Ok = true;
                        result.BrandName = DecodeBase64Utf8(fields[1]);
                        result.LauncherPath = DecodeBase64Utf8(fields[2]);
                        return result;
                    }
                    if (process.ExitCode == 0 && fields.Length == 3 && fields[0] == "OK")
                    {
                        result.Ok = true;
                        result.BrandName = fields[1];
                        result.LauncherPath = fields[2];
                        return result;
                    }
                    string detail = fields.Length >= 2 && fields[0] == "ERROR64"
                        ? DecodeBase64Utf8(fields[1])
                        : (fields.Length >= 2 && fields[0] == "ERROR" ? fields[1] : LastNonEmptyLine(stderr));
                    result.Message = string.IsNullOrWhiteSpace(detail)
                        ? "配置失败，请检查编号和网络。"
                        : "配置失败：" + detail;
                    WriteDiagnostic(stdout, stderr);
                }
            }
            catch (Exception ex)
            {
                result.Message = "配置失败：" + ex.Message;
            }
            return result;
        }

        private void WriteDiagnostic(string stdout, string stderr)
        {
            try
            {
                string logPath = Path.Combine(root, "oem_configurator.log");
                string body = "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "]" + Environment.NewLine
                    + "stdout:" + Environment.NewLine + (stdout ?? "") + Environment.NewLine
                    + "stderr:" + Environment.NewLine + (stderr ?? "") + Environment.NewLine;
                File.AppendAllText(logPath, body, Encoding.UTF8);
            }
            catch
            {
            }
        }

        private static string LastNonEmptyLine(string value)
        {
            string[] lines = (value ?? "").Replace("\r", "").Split(new char[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
            for (int i = lines.Length - 1; i >= 0; i--)
            {
                string line = lines[i].Trim();
                if (line.Length > 0) return line;
            }
            return "";
        }

        private static string DecodeBase64Utf8(string value)
        {
            return Encoding.UTF8.GetString(Convert.FromBase64String((value ?? "").Trim()));
        }

        private static string FindPythonRuntime(string root, out string runtimeArgs)
        {
            runtimeArgs = "";
            string bundled = Path.Combine(root, "python", "python.exe");
            if (File.Exists(bundled)) return bundled;
            string[] names = new string[] { "python.exe", "py.exe" };
            string pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string name in names)
            {
                foreach (string rawDir in pathValue.Split(Path.PathSeparator))
                {
                    string candidate = Path.Combine((rawDir ?? "").Trim().Trim('"'), name);
                    if (File.Exists(candidate))
                    {
                        if (name == "py.exe") runtimeArgs = "-3 ";
                        return candidate;
                    }
                }
            }
            return "";
        }

        private static string Quote(string value)
        {
            return "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            Application.Run(new ConfigForm(root));
        }
    }
}
