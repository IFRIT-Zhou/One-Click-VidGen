using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace OcvLauncher
{
    internal sealed class RuntimeStatus
    {
        public bool BackendOnline;
        public bool FrontendOnline;

        public bool IsRunning
        {
            get { return BackendOnline && FrontendOnline; }
        }
    }

    internal sealed class CheckItem
    {
        public string Name;
        public bool Passed;
        public string Detail;
    }

    internal sealed class LauncherRuntime
    {
        private static readonly int[] ManagedPorts = { 8010, 5173, 8030 };
        private readonly string root;

        public LauncherRuntime()
        {
            root = FindProjectRoot();
        }

        public string Root
        {
            get { return root; }
        }

        public string WorkspaceUrl
        {
            get { return "http://127.0.0.1:5173"; }
        }

        public string LogsDirectory
        {
            get { return Path.Combine(root, "runtime_logs"); }
        }

        public string OutputDirectory
        {
            get { return Path.Combine(root, "output"); }
        }

        private string HostPidFile
        {
            get { return Path.Combine(LogsDirectory, "ocv_launcher_host.pid"); }
        }

        public string StartScript
        {
            get { return Path.Combine(root, "start_windows.bat"); }
        }

        public string VersionText
        {
            get
            {
                string version = ReadPackageVersion();
                string commit = ReadGitCommit();
                if (!string.IsNullOrEmpty(commit)) return "v" + version + " · " + commit;
                return "v" + version + " · 便携版";
            }
        }

        public Process StartServices(Action<string> log)
        {
            if (!File.Exists(StartScript)) throw new FileNotFoundException("找不到 start_windows.bat", StartScript);

            var info = new ProcessStartInfo();
            info.FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe";
            info.Arguments = "/d /s /c \"\"" + StartScript + "\"\"";
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;

            var process = new Process();
            process.StartInfo = info;
            process.EnableRaisingEvents = true;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args)
            {
                if (!string.IsNullOrWhiteSpace(args.Data)) log(args.Data);
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args)
            {
                if (!string.IsNullOrWhiteSpace(args.Data)) log("[stderr] " + args.Data);
            };
            process.Start();
            Directory.CreateDirectory(LogsDirectory);
            File.WriteAllText(
                HostPidFile,
                process.Id.ToString(CultureInfo.InvariantCulture) + "|" + process.StartTime.ToUniversalTime().Ticks.ToString(CultureInfo.InvariantCulture),
                Encoding.ASCII);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }

        public async Task<RuntimeStatus> GetStatusAsync()
        {
            var backendTask = IsPortOpenAsync(8010, 450);
            var frontendTask = IsPortOpenAsync(5173, 450);
            await Task.WhenAll(backendTask, frontendTask);
            return new RuntimeStatus
            {
                BackendOnline = backendTask.Result,
                FrontendOnline = frontendTask.Result
            };
        }

        public async Task<List<CheckItem>> RunEnvironmentCheckAsync(Action<string> log)
        {
            return await Task.Run(delegate
            {
                var items = new List<CheckItem>();
                AddFileCheck(items, "启动脚本", StartScript);
                AddFileCheck(items, "便携 Python", Path.Combine(root, "runtime", "python", "python.exe"));
                AddFileCheck(items, "便携 Node", Path.Combine(root, "runtime", "node", "node.exe"));
                AddFileCheck(items, "便携 npm", Path.Combine(root, "runtime", "node", "npm.cmd"));
                AddFileCheck(items, "FFmpeg", Path.Combine(root, "tools", "ffmpeg", "bin", "ffmpeg.exe"));
                AddDirectoryCheck(items, "根目录依赖", Path.Combine(root, "node_modules", "hyperframes"));
                AddDirectoryCheck(items, "前端依赖", Path.Combine(root, "frontend", "node_modules"));
                AddDirectoryCheck(items, "IndexTTS2 模型", Path.Combine(root, "tools", "IndexTTS2", "checkpoints"));

                string browserRoot = Path.Combine(root, "runtime", "hyperframes", ".cache", "hyperframes", "chrome");
                bool browserFound = Directory.Exists(browserRoot) && Directory.GetFiles(browserRoot, "chrome-headless-shell.exe", SearchOption.AllDirectories).Length > 0;
                items.Add(new CheckItem
                {
                    Name = "渲染浏览器",
                    Passed = browserFound,
                    Detail = browserFound ? "已找到 Chrome Headless Shell" : "缺少内置渲染浏览器"
                });

                string python = Path.Combine(root, "runtime", "python", "python.exe");
                string preflight = Path.Combine(root, "tools", "portable_preflight.py");
                if (File.Exists(python) && File.Exists(preflight))
                {
                    int exitCode;
                    string output = RunAndCapture(python, "\"" + preflight + "\"", root, 120000, out exitCode);
                    items.Add(new CheckItem
                    {
                        Name = "便携路径检查",
                        Passed = exitCode == 0,
                        Detail = exitCode == 0 ? "运行时路径与模型配置正常" : LastMeaningfulLine(output, "便携路径检查失败")
                    });
                    if (!string.IsNullOrWhiteSpace(output)) log(output.Trim());
                }
                else
                {
                    items.Add(new CheckItem { Name = "便携路径检查", Passed = false, Detail = "无法运行 portable_preflight.py" });
                }

                return items;
            });
        }

        public async Task StopServicesAsync(Action<string> log)
        {
            await Task.Run(delegate
            {
                StopRecordedHost(log);
                var pids = FindListeningPids(ManagedPorts);
                foreach (int pid in pids)
                {
                    int exitCode;
                    string output = RunAndCapture("taskkill.exe", "/PID " + pid.ToString(CultureInfo.InvariantCulture) + " /T /F", root, 15000, out exitCode);
                    if (exitCode == 0) log("已停止进程 PID " + pid.ToString(CultureInfo.InvariantCulture));
                    else log("停止 PID " + pid.ToString(CultureInfo.InvariantCulture) + " 失败：" + LastMeaningfulLine(output, "未知错误"));
                }
                try { if (File.Exists(HostPidFile)) File.Delete(HostPidFile); } catch { }
            });
        }

        public void OpenUrl(string url)
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }

        public void OpenFolder(string path)
        {
            Directory.CreateDirectory(path);
            Process.Start(new ProcessStartInfo("explorer.exe", "\"" + path + "\"") { UseShellExecute = true });
        }

        public static string TailFile(string path, int maxLines)
        {
            if (!File.Exists(path)) return string.Empty;
            try
            {
                string text;
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    const int maxTailBytes = 131072;
                    long tailStart = Math.Max(0, stream.Length - maxTailBytes);
                    stream.Seek(tailStart, SeekOrigin.Begin);
                    byte[] buffer = new byte[(int)(stream.Length - tailStart)];
                    int total = 0;
                    while (total < buffer.Length)
                    {
                        int read = stream.Read(buffer, total, buffer.Length - total);
                        if (read <= 0) break;
                        total += read;
                    }
                    text = Encoding.UTF8.GetString(buffer, 0, total);
                    if (tailStart > 0)
                    {
                        int firstLineBreak = text.IndexOf('\n');
                        if (firstLineBreak >= 0) text = text.Substring(firstLineBreak + 1);
                    }
                }
                string[] lines = text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);
                int lineStart = Math.Max(0, lines.Length - maxLines);
                return string.Join(Environment.NewLine, lines, lineStart, lines.Length - lineStart);
            }
            catch { return string.Empty; }
        }

        private static string FindProjectRoot()
        {
            string current = AppDomain.CurrentDomain.BaseDirectory;
            for (int i = 0; i < 6; i++)
            {
                if (File.Exists(Path.Combine(current, "start_windows.bat"))) return current.TrimEnd(Path.DirectorySeparatorChar);
                DirectoryInfo parent = Directory.GetParent(current);
                if (parent == null) break;
                current = parent.FullName;
            }
            return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        }

        private string ReadPackageVersion()
        {
            try
            {
                string text = File.ReadAllText(Path.Combine(root, "package.json"), Encoding.UTF8);
                Match match = Regex.Match(text, "\\\"version\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
                if (match.Success) return match.Groups[1].Value;
            }
            catch { }
            return "未知版本";
        }

        private string ReadGitCommit()
        {
            if (!Directory.Exists(Path.Combine(root, ".git"))) return string.Empty;
            try
            {
                int exitCode;
                string output = RunAndCapture("git.exe", "rev-parse --short HEAD", root, 5000, out exitCode);
                return exitCode == 0 ? output.Trim() : string.Empty;
            }
            catch { return string.Empty; }
        }

        private static async Task<bool> IsPortOpenAsync(int port, int timeoutMs)
        {
            using (var client = new TcpClient())
            {
                try
                {
                    Task connect = client.ConnectAsync("127.0.0.1", port);
                    Task completed = await Task.WhenAny(connect, Task.Delay(timeoutMs));
                    return completed == connect && client.Connected;
                }
                catch { return false; }
            }
        }

        private static void AddFileCheck(List<CheckItem> items, string name, string path)
        {
            bool exists = File.Exists(path);
            items.Add(new CheckItem { Name = name, Passed = exists, Detail = exists ? "正常" : "缺少 " + path });
        }

        private static void AddDirectoryCheck(List<CheckItem> items, string name, string path)
        {
            bool exists = Directory.Exists(path);
            items.Add(new CheckItem { Name = name, Passed = exists, Detail = exists ? "正常" : "缺少 " + path });
        }

        private static HashSet<int> FindListeningPids(int[] ports)
        {
            var result = new HashSet<int>();
            int exitCode;
            string output = RunAndCapture("netstat.exe", "-ano -p tcp", Environment.CurrentDirectory, 10000, out exitCode);
            if (exitCode != 0) return result;

            foreach (string rawLine in output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string line = rawLine.Trim();
                if (!line.StartsWith("TCP", StringComparison.OrdinalIgnoreCase) || line.IndexOf("LISTENING", StringComparison.OrdinalIgnoreCase) < 0) continue;
                string[] parts = Regex.Split(line, "\\s+");
                if (parts.Length < 5) continue;
                int localPort;
                int separator = parts[1].LastIndexOf(':');
                if (separator < 0 || !int.TryParse(parts[1].Substring(separator + 1), out localPort)) continue;
                bool managed = false;
                foreach (int port in ports) if (localPort == port) managed = true;
                int pid;
                if (managed && int.TryParse(parts[4], out pid) && pid > 0) result.Add(pid);
            }
            return result;
        }

        private void StopRecordedHost(Action<string> log)
        {
            if (!File.Exists(HostPidFile)) return;
            try
            {
                string[] parts = File.ReadAllText(HostPidFile, Encoding.ASCII).Trim().Split('|');
                int pid;
                long expectedTicks;
                if (parts.Length != 2 || !int.TryParse(parts[0], out pid) || !long.TryParse(parts[1], out expectedTicks)) return;
                Process host;
                try { host = Process.GetProcessById(pid); }
                catch { return; }
                using (host)
                {
                    long actualTicks;
                    try { actualTicks = host.StartTime.ToUniversalTime().Ticks; }
                    catch { return; }
                    if (Math.Abs(actualTicks - expectedTicks) > TimeSpan.FromSeconds(2).Ticks) return;
                    int exitCode;
                    string output = RunAndCapture("taskkill.exe", "/PID " + pid.ToString(CultureInfo.InvariantCulture) + " /T /F", root, 15000, out exitCode);
                    if (exitCode == 0) log("已停止启动器宿主进程 PID " + pid.ToString(CultureInfo.InvariantCulture));
                    else log("停止启动器宿主失败：" + LastMeaningfulLine(output, "未知错误"));
                }
            }
            finally
            {
                try { File.Delete(HostPidFile); } catch { }
            }
        }

        private static string RunAndCapture(string fileName, string arguments, string workingDirectory, int timeoutMs, out int exitCode)
        {
            var info = new ProcessStartInfo();
            info.FileName = fileName;
            info.Arguments = arguments;
            info.WorkingDirectory = workingDirectory;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;

            using (var process = Process.Start(info))
            {
                string stdout = process.StandardOutput.ReadToEnd();
                string stderr = process.StandardError.ReadToEnd();
                if (!process.WaitForExit(timeoutMs))
                {
                    try { process.Kill(); } catch { }
                    exitCode = -1;
                    return stdout + Environment.NewLine + stderr + Environment.NewLine + "执行超时";
                }
                exitCode = process.ExitCode;
                return (stdout + Environment.NewLine + stderr).Trim();
            }
        }

        private static string LastMeaningfulLine(string text, string fallback)
        {
            if (string.IsNullOrWhiteSpace(text)) return fallback;
            string[] lines = text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            return lines.Length > 0 ? lines[lines.Length - 1].Trim() : fallback;
        }
    }
}
