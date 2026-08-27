using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
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

    internal sealed class UpdateCheckResult
    {
        public string Mode;
        public string CurrentVersion;
        public string LatestVersion;
        public string Message;
        public string ExpectedCommit;
        public string ExpectedReleaseId;
        public string DownloadUrl;
        public List<string> DownloadUrls = new List<string>();
        public string ExpectedArchiveSha256;
        public string ChannelSource;
        public bool CanUpdate;
        public bool IsCurrent;
        public bool IsBlocked;
    }

    internal sealed class UpdateSourceSettings
    {
        public List<string> ChannelUrls = new List<string>();
        public List<string> AllowedDownloadHosts = new List<string>();
        public int PrimaryChannelTimeoutMs = 2500;
        public int FallbackChannelTimeoutMs = 20000;
        public int PrimaryDownloadTimeoutMs = 5000;
        public int FallbackDownloadTimeoutMs = 30000;
        public int DownloadReadWriteTimeoutMs = 120000;
    }

    internal sealed class UpdateLaunchPlan
    {
        public string Mode;
        public string ExpectedCommit;
        public string ExpectedReleaseId;
        public string PackagePath;
    }

    internal sealed class TimeoutWebClient : WebClient
    {
        private readonly int timeoutMs;
        private readonly int readWriteTimeoutMs;

        public TimeoutWebClient(int timeoutMs, int readWriteTimeoutMs)
        {
            this.timeoutMs = Math.Max(1000, timeoutMs);
            this.readWriteTimeoutMs = Math.Max(this.timeoutMs, readWriteTimeoutMs);
        }

        protected override WebRequest GetWebRequest(Uri address)
        {
            WebRequest request = base.GetWebRequest(address);
            request.Timeout = timeoutMs;
            HttpWebRequest httpRequest = request as HttpWebRequest;
            if (httpRequest != null) httpRequest.ReadWriteTimeout = readWriteTimeoutMs;
            return request;
        }
    }

    internal sealed class LauncherRuntime
    {
        private static readonly int[] ManagedPorts = { 8010, 5173, 8030 };
        private static readonly string[] ReleaseIntegrityFiles =
        {
            "OCV_Launcher.exe",
            "start_windows.bat",
            "frontend/src/App.vue",
            "frontend/src/api.js",
            "frontend/src/style.css",
            "backend/app/main.py",
            "story_agents.py",
            "module1_agent_director.py",
            "module2_5_text_corrector.py",
            "module2_scene_director.py",
            "module4_video_render.py",
            "module5_video_render.py",
            "launcher/safe_update_helper.ps1",
            "launcher/update-sources.json"
        };
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

        public string UpdateHistoryDirectory
        {
            get { return Path.Combine(root, "Archives", "launcher_updates"); }
        }

        private string UpdateChannelFile
        {
            get { return Path.Combine(root, "launcher", "update-channel.json"); }
        }

        private string UpdateSourcesFile
        {
            get { return Path.Combine(root, "launcher", "update-sources.json"); }
        }

        private string UpdateHelperFile
        {
            get { return Path.Combine(root, "launcher", "safe_update_helper.ps1"); }
        }

        private string UpdateResultFile
        {
            get { return Path.Combine(LogsDirectory, "launcher_update_result.txt"); }
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
                string release = ReadLocalReleaseId();
                if (!string.IsNullOrEmpty(release)) return "OCV " + release + " · L" + version + " · 便携";
                return "OCV 未标记版本 · L" + version + " · 便携";
            }
        }

        public Process StartServices(Action<string> log)
        {
            if (!File.Exists(StartScript)) throw new FileNotFoundException("找不到 start_windows.bat", StartScript);

            var info = new ProcessStartInfo();
            info.FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe";
            // Run the batch file by name from its working directory.  Passing the
            // absolute path through cmd /c makes directory names such as
            // "One-click VidGen (1)" part of CMD's command grammar, where the
            // parentheses may be interpreted as block delimiters.
            info.Arguments = "/d /s /c call start_windows.bat";
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
                AddDirectoryCheck(items, "IndexTTS-2.5 模型", Path.Combine(root, "tools", "IndexTTS25", "checkpoints"));
                AddDirectoryCheck(items, "Faster-Whisper 模型", Path.Combine(root, "tools", "whisper_models", "faster-whisper-base"));

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

        public async Task<UpdateCheckResult> CheckForUpdatesAsync(Action<string> log)
        {
            return await Task.Run(delegate
            {
                try
                {
                    if (Directory.Exists(Path.Combine(root, ".git")))
                    {
                        return CheckGitUpdate(log);
                    }
                    return CheckPortableUpdate(log);
                }
                catch (Exception ex)
                {
                    return new UpdateCheckResult
                    {
                        Mode = Directory.Exists(Path.Combine(root, ".git")) ? "git" : "portable",
                        Message = "检查更新失败：" + ex.Message,
                        IsBlocked = true
                    };
                }
            });
        }

        public async Task<UpdateLaunchPlan> PrepareUpdateAsync(UpdateCheckResult update, Action<string> log)
        {
            if (update == null || !update.CanUpdate) throw new InvalidOperationException("当前没有可安装的安全更新。");
            if (update.Mode == "git")
            {
                return new UpdateLaunchPlan
                {
                    Mode = "git",
                    ExpectedCommit = update.ExpectedCommit ?? string.Empty
                };
            }

            string updateDirectory = Path.Combine(root, "runtime", "temp", "ocv-updates");
            Directory.CreateDirectory(updateDirectory);
            string packagePath = Path.Combine(updateDirectory, "ocv_update_" + SanitizeFileName(update.ExpectedReleaseId) + ".zip");
            var downloadUrls = new List<string>();
            if (update.DownloadUrls != null) AddDistinct(downloadUrls, update.DownloadUrls);
            if (!string.IsNullOrWhiteSpace(update.DownloadUrl)) AddDistinct(downloadUrls, new[] { update.DownloadUrl });
            if (downloadUrls.Count == 0) throw new InvalidOperationException("远端更新通道没有提供可用的更新包地址。");

            UpdateSourceSettings sourceSettings = LoadUpdateSourceSettings();
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            var failures = new List<string>();
            bool downloaded = false;
            for (int index = 0; index < downloadUrls.Count; index++)
            {
                string candidate = downloadUrls[index];
                Uri downloadUri;
                if (!TryCreateTrustedDownloadUri(candidate, sourceSettings, out downloadUri))
                {
                    failures.Add("下载源 " + (index + 1).ToString(CultureInfo.InvariantCulture) + " 不是允许的 HTTPS 地址");
                    continue;
                }
                try
                {
                    if (File.Exists(packagePath)) File.Delete(packagePath);
                    log("正在尝试更新包下载源 " + (index + 1).ToString(CultureInfo.InvariantCulture) + "/" + downloadUrls.Count.ToString(CultureInfo.InvariantCulture) + "：" + downloadUri.Host);
                    int timeoutMs = index == 0 ? sourceSettings.PrimaryDownloadTimeoutMs : sourceSettings.FallbackDownloadTimeoutMs;
                    using (var client = CreateWebClient(timeoutMs, sourceSettings.DownloadReadWriteTimeoutMs))
                    {
                        await client.DownloadFileTaskAsync(downloadUri, packagePath);
                    }
                    ValidateDownloadedPackage(packagePath, update.ExpectedArchiveSha256);
                    downloaded = true;
                    break;
                }
                catch (Exception ex)
                {
                    try { if (File.Exists(packagePath)) File.Delete(packagePath); } catch { }
                    failures.Add(downloadUri.Host + "：" + CompactNetworkError(ex));
                    if (index + 1 < downloadUrls.Count) log("当前下载源不可用，立即切换备用源……");
                }
            }
            if (!downloaded) throw new InvalidOperationException("所有更新包下载源均不可用：" + string.Join("；", failures.ToArray()));

            var package = new FileInfo(packagePath);
            log("更新包下载完成：" + Math.Round(package.Length / 1024d / 1024d, 1).ToString(CultureInfo.InvariantCulture) + " MB");
            return new UpdateLaunchPlan
            {
                Mode = "portable",
                ExpectedReleaseId = update.ExpectedReleaseId ?? string.Empty,
                PackagePath = packagePath
            };
        }

        public Process StartUpdateHelper(UpdateLaunchPlan plan, int launcherPid)
        {
            if (plan == null) throw new ArgumentNullException("plan");
            if (!File.Exists(UpdateHelperFile)) throw new FileNotFoundException("找不到安全更新助手", UpdateHelperFile);
            var arguments = new StringBuilder();
            arguments.Append("-NoProfile -ExecutionPolicy Bypass -File ").Append(QuoteArgument(UpdateHelperFile));
            arguments.Append(" -Mode ").Append(QuoteArgument(plan.Mode));
            arguments.Append(" -ProjectRoot ").Append(QuoteArgument(root));
            arguments.Append(" -LauncherPid ").Append(launcherPid.ToString(CultureInfo.InvariantCulture));
            if (!string.IsNullOrWhiteSpace(plan.ExpectedCommit)) arguments.Append(" -ExpectedCommit ").Append(QuoteArgument(plan.ExpectedCommit));
            if (!string.IsNullOrWhiteSpace(plan.ExpectedReleaseId)) arguments.Append(" -ExpectedReleaseId ").Append(QuoteArgument(plan.ExpectedReleaseId));
            if (!string.IsNullOrWhiteSpace(plan.PackagePath)) arguments.Append(" -PackagePath ").Append(QuoteArgument(plan.PackagePath));

            var info = new ProcessStartInfo();
            info.FileName = "powershell.exe";
            info.Arguments = arguments.ToString();
            info.WorkingDirectory = root;
            info.UseShellExecute = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            return Process.Start(info);
        }

        public string ConsumeUpdateResult()
        {
            try
            {
                if (!File.Exists(UpdateResultFile)) return string.Empty;
                string value = File.ReadAllText(UpdateResultFile, Encoding.UTF8).Trim();
                File.Delete(UpdateResultFile);
                return value;
            }
            catch { return string.Empty; }
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

        private UpdateCheckResult CheckGitUpdate(Action<string> log)
        {
            int exitCode;
            string dirty = RunAndCapture("git.exe", "status --porcelain", root, 10000, out exitCode);
            if (exitCode != 0)
            {
                return BlockedUpdate("git", "Git 仓库无法读取，请确认系统 Git 可用。", dirty);
            }
            if (!string.IsNullOrWhiteSpace(dirty))
            {
                return BlockedUpdate("git", "检测到尚未提交的本地改动。为防止覆盖，安全更新已锁定。", string.Empty);
            }

            log("正在获取 origin/main 的最新版本信息……");
            string fetch = RunAndCapture("git.exe", "fetch --quiet origin main", root, 90000, out exitCode);
            if (exitCode != 0) return BlockedUpdate("git", "无法连接 GitHub 或获取 origin/main。", LastMeaningfulLine(fetch, "Git fetch 失败"));

            string current = RunGitValue("rev-parse HEAD", 10000);
            string latest = RunGitValue("rev-parse FETCH_HEAD", 10000);
            string common = RunGitValue("merge-base HEAD FETCH_HEAD", 10000);
            string currentShort = ShortCommit(current);
            string latestShort = ShortCommit(latest);
            if (string.Equals(current, latest, StringComparison.OrdinalIgnoreCase))
            {
                return new UpdateCheckResult
                {
                    Mode = "git",
                    CurrentVersion = currentShort,
                    LatestVersion = latestShort,
                    Message = "当前已经是 origin/main 最新版本。",
                    IsCurrent = true
                };
            }
            if (string.Equals(common, current, StringComparison.OrdinalIgnoreCase))
            {
                return new UpdateCheckResult
                {
                    Mode = "git",
                    CurrentVersion = currentShort,
                    LatestVersion = latestShort,
                    Message = "发现可安全快进的新版本。更新时不会改动用户数据。",
                    ExpectedCommit = latest,
                    CanUpdate = true
                };
            }
            if (string.Equals(common, latest, StringComparison.OrdinalIgnoreCase))
            {
                return BlockedUpdate("git", "本地版本领先于 origin/main，属于开发者版本，不执行自动降级。", currentShort + " > " + latestShort);
            }
            return BlockedUpdate("git", "本地与 origin/main 已分叉，请由开发者手动合并，安全更新不会强制覆盖。", currentShort + " / " + latestShort);
        }

        private UpdateCheckResult CheckPortableUpdate(Action<string> log)
        {
            if (!File.Exists(UpdateChannelFile)) return BlockedUpdate("portable", "当前便携包缺少更新通道文件，请使用新版完整包升级一次。", string.Empty);
            string localJson = File.ReadAllText(UpdateChannelFile, Encoding.UTF8);
            string localRelease = ReadJsonString(localJson, "release_id");
            long localOrder = ReadJsonLong(localJson, "release_order");
            string localDisplay = ReadJsonString(localJson, "display_version");
            if (string.IsNullOrWhiteSpace(localRelease) || localOrder <= 0) return BlockedUpdate("portable", "本地更新通道信息无效。", string.Empty);

            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            string remoteUrl;
            string remoteJson = DownloadFirstValidUpdateChannel(LoadUpdateSourceSettings(), log, out remoteUrl);
            string remoteRelease = ReadJsonString(remoteJson, "release_id");
            long remoteOrder = ReadJsonLong(remoteJson, "release_order");
            string remoteDisplay = ReadJsonString(remoteJson, "display_version");
            string archiveUrl = ReadJsonString(remoteJson, "archive_url");
            List<string> archiveUrls = ReadJsonStringArray(remoteJson, "archive_urls");
            AddDistinct(archiveUrls, new[] { ReadJsonString(remoteJson, "archive_url_cn"), archiveUrl });
            string archiveSha256 = ReadJsonString(remoteJson, "archive_sha256");
            string expectedFingerprint = ReadJsonString(remoteJson, "content_fingerprint");
            bool portableOverlaySafe = ReadJsonBool(remoteJson, "portable_overlay_safe");
            long portableOverlayMinOrder = ReadJsonLong(remoteJson, "portable_overlay_min_order");
            if (string.IsNullOrWhiteSpace(remoteRelease) || remoteOrder <= 0) return BlockedUpdate("portable", "远端更新通道返回了无效信息。", string.Empty);

            if (remoteOrder == localOrder)
            {
                if (!string.IsNullOrWhiteSpace(expectedFingerprint))
                {
                    string localFingerprint = ComputeReleaseContentFingerprint();
                    if (!string.Equals(localFingerprint, expectedFingerprint, StringComparison.OrdinalIgnoreCase))
                    {
                        return new UpdateCheckResult
                        {
                            Mode = "portable",
                            CurrentVersion = localDisplay,
                            LatestVersion = remoteDisplay,
                            Message = "版本号一致，但关键程序文件不完整或不匹配。可执行安全修复更新。",
                            ExpectedReleaseId = remoteRelease,
                            DownloadUrl = archiveUrl,
                            DownloadUrls = archiveUrls,
                            ExpectedArchiveSha256 = archiveSha256,
                            ChannelSource = remoteUrl,
                            CanUpdate = true
                        };
                    }
                }
                return new UpdateCheckResult
                {
                    Mode = "portable",
                    CurrentVersion = localDisplay,
                    LatestVersion = remoteDisplay,
                    Message = "当前便携版已经是最新安全版本。",
                    IsCurrent = true
                };
            }
            if (remoteOrder < localOrder)
            {
                return BlockedUpdate("portable", "本地便携版比公开更新通道更新，不执行自动降级。", localDisplay);
            }
            bool portableBaselineCompatible = portableOverlayMinOrder > 0 && localOrder >= portableOverlayMinOrder;
            if (!portableOverlaySafe && !portableBaselineCompatible)
            {
                return BlockedUpdate("portable", "该版本包含运行环境、依赖或模型变更，不能安全覆盖。请下载新的完整便携包。", remoteDisplay);
            }
            return new UpdateCheckResult
            {
                Mode = "portable",
                CurrentVersion = localDisplay,
                LatestVersion = remoteDisplay,
                Message = "发现新的便携版源码更新。运行环境、模型与用户数据将被保留。",
                ExpectedReleaseId = remoteRelease,
                DownloadUrl = archiveUrl,
                DownloadUrls = archiveUrls,
                ExpectedArchiveSha256 = archiveSha256,
                ChannelSource = remoteUrl,
                CanUpdate = true
            };
        }

        private UpdateSourceSettings LoadUpdateSourceSettings()
        {
            var settings = new UpdateSourceSettings();
            settings.ChannelUrls.Add("https://download.oneclickvidgen.com/launcher/update-channel.json");
            settings.ChannelUrls.Add("https://modelscope.cn/models/IFRIT95/One-Click-VidGen-Update-Mirror/resolve/master/launcher/update-channel.json");
            settings.ChannelUrls.Add("https://raw.githubusercontent.com/IFRIT-Zhou/One-Click-VidGen/main/launcher/update-channel.json");
            settings.AllowedDownloadHosts.Add("*.oneclickvidgen.com");
            settings.AllowedDownloadHosts.Add("github.com");
            settings.AllowedDownloadHosts.Add("codeload.github.com");
            settings.AllowedDownloadHosts.Add("*.githubusercontent.com");
            settings.AllowedDownloadHosts.Add("gitee.com");
            settings.AllowedDownloadHosts.Add("*.gitee.com");
            settings.AllowedDownloadHosts.Add("modelscope.cn");
            settings.AllowedDownloadHosts.Add("*.modelscope.cn");
            settings.AllowedDownloadHosts.Add("modelscope.ai");
            settings.AllowedDownloadHosts.Add("*.modelscope.ai");
            settings.AllowedDownloadHosts.Add("*.aliyuncs.com");

            if (!File.Exists(UpdateSourcesFile)) return settings;
            try
            {
                string json = File.ReadAllText(UpdateSourcesFile, Encoding.UTF8);
                List<string> channelUrls = ReadJsonStringArray(json, "channel_urls");
                List<string> allowedHosts = ReadJsonStringArray(json, "allowed_download_hosts");
                if (channelUrls.Count > 0)
                {
                    settings.ChannelUrls.Clear();
                    AddDistinct(settings.ChannelUrls, channelUrls);
                }
                if (allowedHosts.Count > 0)
                {
                    settings.AllowedDownloadHosts.Clear();
                    AddDistinct(settings.AllowedDownloadHosts, allowedHosts);
                }
                settings.PrimaryChannelTimeoutMs = ReadJsonInt(json, "primary_channel_timeout_ms", settings.PrimaryChannelTimeoutMs, 1000, 30000);
                settings.FallbackChannelTimeoutMs = ReadJsonInt(json, "fallback_channel_timeout_ms", settings.FallbackChannelTimeoutMs, 2000, 60000);
                settings.PrimaryDownloadTimeoutMs = ReadJsonInt(json, "primary_download_timeout_ms", settings.PrimaryDownloadTimeoutMs, 2000, 60000);
                settings.FallbackDownloadTimeoutMs = ReadJsonInt(json, "fallback_download_timeout_ms", settings.FallbackDownloadTimeoutMs, 5000, 120000);
                settings.DownloadReadWriteTimeoutMs = ReadJsonInt(json, "download_read_write_timeout_ms", settings.DownloadReadWriteTimeoutMs, 30000, 600000);
            }
            catch
            {
                // A malformed optional source file must not disable the compiled GitHub fallback.
            }
            return settings;
        }

        private string DownloadFirstValidUpdateChannel(UpdateSourceSettings settings, Action<string> log, out string selectedUrl)
        {
            selectedUrl = string.Empty;
            var failures = new List<string>();
            for (int index = 0; index < settings.ChannelUrls.Count; index++)
            {
                string candidate = settings.ChannelUrls[index];
                Uri uri;
                if (!TryCreateTrustedDownloadUri(candidate, settings, out uri))
                {
                    failures.Add("更新源 " + (index + 1).ToString(CultureInfo.InvariantCulture) + " 不是允许的 HTTPS 地址");
                    continue;
                }
                try
                {
                    log("正在检查更新源 " + (index + 1).ToString(CultureInfo.InvariantCulture) + "/" + settings.ChannelUrls.Count.ToString(CultureInfo.InvariantCulture) + "：" + uri.Host);
                    int timeoutMs = index == 0 ? settings.PrimaryChannelTimeoutMs : settings.FallbackChannelTimeoutMs;
                    string json;
                    using (var client = CreateWebClient(timeoutMs, timeoutMs)) json = client.DownloadString(uri);
                    if (ReadJsonLong(json, "release_order") <= 0 || string.IsNullOrWhiteSpace(ReadJsonString(json, "release_id")))
                    {
                        throw new InvalidDataException("更新清单格式无效");
                    }
                    selectedUrl = uri.AbsoluteUri;
                    if (index > 0) log("已切换到备用更新源：" + uri.Host);
                    return json;
                }
                catch (Exception ex)
                {
                    failures.Add(uri.Host + "：" + CompactNetworkError(ex));
                    if (index + 1 < settings.ChannelUrls.Count) log("当前更新源不可用，立即切换备用源……");
                }
            }
            throw new InvalidOperationException("所有更新通道均不可用：" + string.Join("；", failures.ToArray()));
        }

        private static bool TryCreateTrustedDownloadUri(string value, UpdateSourceSettings settings, out Uri uri)
        {
            uri = null;
            Uri candidate;
            if (!Uri.TryCreate(value, UriKind.Absolute, out candidate)) return false;
            if (!string.Equals(candidate.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)) return false;
            foreach (string allowedHost in settings.AllowedDownloadHosts)
            {
                if (HostMatches(candidate.Host, allowedHost))
                {
                    uri = candidate;
                    return true;
                }
            }
            return false;
        }

        private static bool HostMatches(string host, string pattern)
        {
            string normalizedHost = (host ?? string.Empty).Trim().TrimEnd('.');
            string normalizedPattern = (pattern ?? string.Empty).Trim().TrimEnd('.');
            if (normalizedPattern.StartsWith("*.", StringComparison.Ordinal))
            {
                string suffix = normalizedPattern.Substring(1);
                return normalizedHost.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)
                    && normalizedHost.Length > suffix.Length;
            }
            return string.Equals(normalizedHost, normalizedPattern, StringComparison.OrdinalIgnoreCase);
        }

        private static void ValidateDownloadedPackage(string packagePath, string expectedSha256)
        {
            var package = new FileInfo(packagePath);
            if (!package.Exists || package.Length < 102400) throw new InvalidDataException("下载的更新包无效或不完整");
            using (var stream = new FileStream(packagePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.ReadByte() != 'P' || stream.ReadByte() != 'K') throw new InvalidDataException("下载内容不是有效 ZIP 文件");
            }
            if (!string.IsNullOrWhiteSpace(expectedSha256))
            {
                string normalized = expectedSha256.Trim().ToLowerInvariant();
                if (!Regex.IsMatch(normalized, "^[0-9a-f]{64}$")) throw new InvalidDataException("更新清单中的 archive_sha256 格式无效");
                string actual = ComputeFileSha256(packagePath);
                if (!string.Equals(actual, normalized, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("更新包 SHA-256 校验失败");
            }
        }

        private static string CompactNetworkError(Exception ex)
        {
            if (ex == null) return "未知错误";
            WebException webException = ex as WebException;
            if (webException != null) return webException.Status + " · " + webException.Message;
            return ex.Message;
        }

        private static void AddDistinct(List<string> target, IEnumerable<string> values)
        {
            if (values == null) return;
            foreach (string rawValue in values)
            {
                string value = (rawValue ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(value)) continue;
                bool exists = false;
                foreach (string current in target)
                {
                    if (string.Equals(current, value, StringComparison.OrdinalIgnoreCase))
                    {
                        exists = true;
                        break;
                    }
                }
                if (!exists) target.Add(value);
            }
        }

        private string ReadLocalReleaseId()
        {
            try
            {
                if (!File.Exists(UpdateChannelFile)) return string.Empty;
                return ReadJsonString(File.ReadAllText(UpdateChannelFile, Encoding.UTF8), "release_id");
            }
            catch
            {
                return string.Empty;
            }
        }

        private string ComputeReleaseContentFingerprint()
        {
            var summary = new StringBuilder();
            foreach (string relativePath in ReleaseIntegrityFiles)
            {
                string fullPath = Path.Combine(root, relativePath.Replace('/', Path.DirectorySeparatorChar));
                string fileHash = File.Exists(fullPath) ? ComputeFileSha256(fullPath) : "MISSING";
                summary.Append(relativePath.Replace('\\', '/'));
                summary.Append('|');
                summary.Append(fileHash);
                summary.Append('\n');
            }
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(summary.ToString()));
                return BytesToHex(digest);
            }
        }

        private static string ComputeFileSha256(string path)
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
            {
                return BytesToHex(sha.ComputeHash(stream));
            }
        }

        private static string BytesToHex(byte[] bytes)
        {
            var result = new StringBuilder(bytes.Length * 2);
            foreach (byte value in bytes) result.Append(value.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private string RunGitValue(string arguments, int timeoutMs)
        {
            int exitCode;
            string output = RunAndCapture("git.exe", arguments, root, timeoutMs, out exitCode);
            if (exitCode != 0 || string.IsNullOrWhiteSpace(output)) throw new InvalidOperationException("Git 命令失败：git " + arguments + " · " + LastMeaningfulLine(output, "无输出"));
            return output.Trim();
        }

        private static UpdateCheckResult BlockedUpdate(string mode, string message, string detail)
        {
            return new UpdateCheckResult
            {
                Mode = mode,
                Message = string.IsNullOrWhiteSpace(detail) ? message : message + "（" + detail + "）",
                IsBlocked = true
            };
        }

        private static TimeoutWebClient CreateWebClient(int timeoutMs, int readWriteTimeoutMs)
        {
            var client = new TimeoutWebClient(timeoutMs, readWriteTimeoutMs);
            client.Encoding = Encoding.UTF8;
            client.Headers[HttpRequestHeader.UserAgent] = "One-Click-VidGen-Launcher/2";
            client.Headers[HttpRequestHeader.Accept] = "application/json, text/plain, */*";
            return client;
        }

        private static string ReadJsonString(string json, string key)
        {
            Match match = Regex.Match(json ?? string.Empty, "\\\"" + Regex.Escape(key) + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"", RegexOptions.IgnoreCase);
            return match.Success ? Regex.Unescape(match.Groups[1].Value) : string.Empty;
        }

        internal static List<string> ReadJsonStringArray(string json, string key)
        {
            var values = new List<string>();
            Match arrayMatch = Regex.Match(
                json ?? string.Empty,
                "\\\"" + Regex.Escape(key) + "\\\"\\s*:\\s*\\[(.*?)\\]",
                RegexOptions.IgnoreCase | RegexOptions.Singleline);
            if (!arrayMatch.Success) return values;
            foreach (Match valueMatch in Regex.Matches(arrayMatch.Groups[1].Value, "\\\"((?:\\\\.|[^\\\"])*)\\\""))
            {
                string value = Regex.Unescape(valueMatch.Groups[1].Value).Trim();
                if (!string.IsNullOrWhiteSpace(value)) values.Add(value);
            }
            return values;
        }

        private static long ReadJsonLong(string json, string key)
        {
            Match match = Regex.Match(json ?? string.Empty, "\\\"" + Regex.Escape(key) + "\\\"\\s*:\\s*(\\d+)", RegexOptions.IgnoreCase);
            long value;
            return match.Success && long.TryParse(match.Groups[1].Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : 0;
        }

        private static int ReadJsonInt(string json, string key, int fallback, int minimum, int maximum)
        {
            long value = ReadJsonLong(json, key);
            if (value <= 0) return fallback;
            return (int)Math.Max(minimum, Math.Min(maximum, value));
        }

        private static bool ReadJsonBool(string json, string key)
        {
            Match match = Regex.Match(json ?? string.Empty, "\\\"" + Regex.Escape(key) + "\\\"\\s*:\\s*(true|false)", RegexOptions.IgnoreCase);
            return match.Success && string.Equals(match.Groups[1].Value, "true", StringComparison.OrdinalIgnoreCase);
        }

        private static string ShortCommit(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "未知";
            return value.Length > 8 ? value.Substring(0, 8) : value;
        }

        private static string SanitizeFileName(string value)
        {
            string safe = Regex.Replace(value ?? string.Empty, "[^0-9A-Za-z._-]+", "_");
            return string.IsNullOrWhiteSpace(safe) ? DateTime.Now.ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture) : safe;
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + (value ?? string.Empty).Replace("\"", "\\\"") + "\"";
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
