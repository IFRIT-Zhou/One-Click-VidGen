using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace OcvLauncher
{
    internal sealed class MainForm : Form
    {
        private static readonly Color Bg = Color.FromArgb(7, 17, 31);
        private static readonly Color Panel = Color.FromArgb(14, 29, 49);
        private static readonly Color PanelSoft = Color.FromArgb(19, 39, 63);
        private static readonly Color Border = Color.FromArgb(37, 69, 96);
        private static readonly Color TextMain = Color.FromArgb(232, 243, 255);
        private static readonly Color TextMuted = Color.FromArgb(145, 169, 194);
        private static readonly Color Cyan = Color.FromArgb(83, 211, 242);
        private static readonly Color Purple = Color.FromArgb(133, 105, 255);
        private static readonly Color Green = Color.FromArgb(45, 212, 191);
        private static readonly Color Amber = Color.FromArgb(251, 191, 36);
        private static readonly Color Red = Color.FromArgb(248, 113, 113);

        private readonly LauncherRuntime runtime;
        private readonly Timer statusTimer;
        private readonly Dictionary<string, long> logOffsets = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
        private Process serviceProcess;
        private bool busy;
        private bool closingAfterPrompt;

        private Label statusPill;
        private Label statusTitle;
        private Label statusDetail;
        private Label versionLabel;
        private Label backendValue;
        private Label frontendValue;
        private Label environmentValue;
        private Button startButton;
        private Button stopButton;
        private Button restartButton;
        private Button checkButton;
        private RichTextBox logBox;
        private FlowLayoutPanel checkResults;
        private ProgressBar busyProgress;

        public MainForm()
        {
            runtime = new LauncherRuntime();
            statusTimer = new Timer();
            statusTimer.Interval = 1800;
            statusTimer.Tick += StatusTimerTick;

            BuildWindow();
            BuildInterface();
            Shown += MainFormShown;
            FormClosing += MainFormClosing;
        }

        private void BuildWindow()
        {
            Text = "OCV 启动管理器";
            BackColor = Bg;
            ForeColor = TextMain;
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            MinimumSize = new Size(900, 620);
            Rectangle workingArea = Screen.PrimaryScreen.WorkingArea;
            Size = new Size(
                Math.Min(1180, Math.Max(900, workingArea.Width - 48)),
                Math.Min(790, Math.Max(620, workingArea.Height - 48)));
            if (workingArea.Width < 1180 || workingArea.Height < 760) WindowState = FormWindowState.Maximized;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            Icon = SystemIcons.Application;
        }

        private void BuildInterface()
        {
            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Padding = new Padding(18);
            root.BackColor = Bg;
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 92F));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 60F));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 40F));
            Controls.Add(root);

            root.Controls.Add(BuildHeader(), 0, 0);
            root.Controls.Add(BuildMainArea(), 0, 1);
            root.Controls.Add(BuildLogArea(), 0, 2);
        }

        private Control BuildHeader()
        {
            var header = CardPanel();
            header.Padding = new Padding(18, 12, 18, 12);

            var table = new TableLayoutPanel();
            table.Dock = DockStyle.Fill;
            table.ColumnCount = 3;
            table.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 66F));
            table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            table.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 210F));
            header.Controls.Add(table);

            var logo = new PictureBox();
            logo.Dock = DockStyle.Fill;
            logo.SizeMode = PictureBoxSizeMode.Zoom;
            logo.Margin = new Padding(0, 1, 12, 1);
            logo.Image = LoadEmbeddedLogo();
            table.Controls.Add(logo, 0, 0);

            var titlePanel = new TableLayoutPanel();
            titlePanel.Dock = DockStyle.Fill;
            titlePanel.RowCount = 2;
            titlePanel.RowStyles.Add(new RowStyle(SizeType.Percent, 58F));
            titlePanel.RowStyles.Add(new RowStyle(SizeType.Percent, 42F));
            titlePanel.Controls.Add(NewLabel("一键成片 · OCV Launcher", 16F, FontStyle.Bold, TextMain), 0, 0);
            titlePanel.Controls.Add(NewLabel("启动、体检与管理 One-Click VidGen", 8.5F, FontStyle.Regular, TextMuted), 0, 1);
            table.Controls.Add(titlePanel, 1, 0);

            var versionPanel = new TableLayoutPanel();
            versionPanel.Dock = DockStyle.Fill;
            versionPanel.RowCount = 2;
            versionPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 48F));
            versionPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 52F));
            versionLabel = NewLabel(runtime.VersionText, 9F, FontStyle.Bold, TextMuted);
            versionLabel.TextAlign = ContentAlignment.MiddleRight;
            statusPill = NewLabel("正在检测", 9F, FontStyle.Bold, Amber);
            statusPill.TextAlign = ContentAlignment.MiddleRight;
            versionPanel.Controls.Add(versionLabel, 0, 0);
            versionPanel.Controls.Add(statusPill, 0, 1);
            table.Controls.Add(versionPanel, 2, 0);

            return header;
        }

        private Control BuildMainArea()
        {
            var main = new TableLayoutPanel();
            main.Dock = DockStyle.Fill;
            main.Padding = new Padding(0, 12, 0, 8);
            main.ColumnCount = 2;
            main.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 55F));
            main.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 45F));
            main.RowCount = 1;
            main.Controls.Add(BuildLaunchCard(), 0, 0);
            main.Controls.Add(BuildStatusCard(), 1, 0);
            return main;
        }

        private Control BuildLaunchCard()
        {
            var card = CardPanel();
            card.Margin = new Padding(0, 0, 7, 0);
            card.Padding = new Padding(22, 16, 22, 14);

            var layout = new TableLayoutPanel();
            layout.Dock = DockStyle.Fill;
            layout.RowCount = 6;
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 26F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 62F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 50F));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 8F));
            card.Controls.Add(layout);

            layout.Controls.Add(NewLabel("工作台控制", 9F, FontStyle.Bold, Cyan), 0, 0);
            statusTitle = NewLabel("正在读取运行状态", 16F, FontStyle.Bold, TextMain);
            layout.Controls.Add(statusTitle, 0, 1);
            statusDetail = NewLabel("管理器会检测本地前端与后端，不会读取或修改你的作品。", 9F, FontStyle.Regular, TextMuted);
            statusDetail.MaximumSize = new Size(620, 0);
            statusDetail.AutoSize = true;
            layout.Controls.Add(statusDetail, 0, 2);

            startButton = AccentButton("▶  启动 OCV", Cyan, Color.FromArgb(4, 16, 29));
            startButton.Font = new Font(Font.FontFamily, 13F, FontStyle.Bold);
            startButton.Click += StartButtonClick;
            layout.Controls.Add(startButton, 0, 3);

            var controls = new FlowLayoutPanel();
            controls.Dock = DockStyle.Fill;
            controls.FlowDirection = FlowDirection.LeftToRight;
            controls.WrapContents = true;
            controls.Padding = new Padding(0, 8, 0, 0);
            stopButton = SecondaryButton("■  停止服务");
            restartButton = SecondaryButton("↻  重新启动");
            var openButton = SecondaryButton("打开工作台");
            stopButton.Click += StopButtonClick;
            restartButton.Click += RestartButtonClick;
            openButton.Click += delegate { SafeOpenWorkspace(); };
            controls.Controls.Add(stopButton);
            controls.Controls.Add(restartButton);
            controls.Controls.Add(openButton);
            layout.Controls.Add(controls, 0, 4);

            busyProgress = new ProgressBar();
            busyProgress.Dock = DockStyle.Bottom;
            busyProgress.Style = ProgressBarStyle.Marquee;
            busyProgress.MarqueeAnimationSpeed = 22;
            busyProgress.Visible = false;
            layout.Controls.Add(busyProgress, 0, 5);
            return card;
        }

        private Control BuildStatusCard()
        {
            var card = CardPanel();
            card.Margin = new Padding(7, 0, 0, 0);
            card.Padding = new Padding(18, 14, 18, 14);

            var layout = new TableLayoutPanel();
            layout.Dock = DockStyle.Fill;
            layout.RowCount = 7;
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 54F));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42F));
            card.Controls.Add(layout);

            layout.Controls.Add(NewLabel("状态与快捷入口", 11F, FontStyle.Bold, TextMain), 0, 0);
            backendValue = AddStatusRow(layout, 1, "本地后端", "检测中");
            frontendValue = AddStatusRow(layout, 2, "网页前端", "检测中");
            environmentValue = AddStatusRow(layout, 3, "便携环境", "尚未体检");

            checkButton = AccentButton("⚡  运行环境体检", Purple, Color.White);
            checkButton.Click += CheckButtonClick;
            checkButton.Margin = new Padding(0, 6, 0, 6);
            layout.Controls.Add(checkButton, 0, 4);

            checkResults = new FlowLayoutPanel();
            checkResults.Dock = DockStyle.Fill;
            checkResults.AutoScroll = true;
            checkResults.FlowDirection = FlowDirection.TopDown;
            checkResults.WrapContents = false;
            checkResults.BackColor = Color.Transparent;
            layout.Controls.Add(checkResults, 0, 5);

            var quick = new FlowLayoutPanel();
            quick.Dock = DockStyle.Fill;
            quick.FlowDirection = FlowDirection.LeftToRight;
            quick.WrapContents = false;
            var outputButton = SecondaryButton("打开输出目录");
            var logsButton = SecondaryButton("打开日志目录");
            outputButton.Click += delegate { runtime.OpenFolder(runtime.OutputDirectory); };
            logsButton.Click += delegate { runtime.OpenFolder(runtime.LogsDirectory); };
            quick.Controls.Add(outputButton);
            quick.Controls.Add(logsButton);
            layout.Controls.Add(quick, 0, 6);
            return card;
        }

        private Control BuildLogArea()
        {
            var card = CardPanel();
            card.Padding = new Padding(18, 12, 18, 14);

            var layout = new TableLayoutPanel();
            layout.Dock = DockStyle.Fill;
            layout.RowCount = 2;
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34F));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            card.Controls.Add(layout);

            var toolbar = new TableLayoutPanel();
            toolbar.Dock = DockStyle.Fill;
            toolbar.ColumnCount = 2;
            toolbar.RowCount = 1;
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 220F));
            toolbar.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            var logTitle = NewLabel("实时启动日志", 10F, FontStyle.Bold, TextMain);
            logTitle.Margin = new Padding(2, 0, 0, 0);
            toolbar.Controls.Add(logTitle, 0, 0);
            var buttons = new FlowLayoutPanel();
            buttons.Dock = DockStyle.Fill;
            buttons.FlowDirection = FlowDirection.RightToLeft;
            var clear = SmallButton("清空");
            var recent = SmallButton("载入最近日志");
            clear.Click += delegate { logBox.Clear(); };
            recent.Click += delegate { LoadRecentLogs(); };
            buttons.Controls.Add(clear);
            buttons.Controls.Add(recent);
            toolbar.Controls.Add(buttons, 1, 0);
            layout.Controls.Add(toolbar, 0, 0);

            logBox = new RichTextBox();
            logBox.Dock = DockStyle.Fill;
            logBox.ReadOnly = true;
            logBox.BorderStyle = BorderStyle.None;
            logBox.BackColor = Color.FromArgb(5, 13, 25);
            logBox.ForeColor = Color.FromArgb(178, 207, 228);
            logBox.Font = new Font("Consolas", 9F, FontStyle.Regular);
            logBox.DetectUrls = false;
            logBox.WordWrap = false;
            layout.Controls.Add(logBox, 0, 1);
            return card;
        }

        private async void MainFormShown(object sender, EventArgs e)
        {
            AppendLog("OCV Launcher 第一阶段已启动。");
            AppendLog("项目目录：" + runtime.Root);
            InitializeLogOffsets(true);
            LoadRecentLogs();
            await RefreshRuntimeStatus();
            statusTimer.Start();
        }

        private async void StatusTimerTick(object sender, EventArgs e)
        {
            statusTimer.Stop();
            try
            {
                await RefreshRuntimeStatus();
                ReadNewServiceLogs();
            }
            finally
            {
                if (!IsDisposed) statusTimer.Start();
            }
        }

        private async void StartButtonClick(object sender, EventArgs e)
        {
            if (busy) return;
            RuntimeStatus current = await runtime.GetStatusAsync();
            if (current.IsRunning)
            {
                SafeOpenWorkspace();
                return;
            }

            SetBusy(true, "正在启动 OCV");
            // Existing service logs can be very large. Follow only content written
            // after this launch; historical context is available via the explicit
            // "载入最近日志" button.
            InitializeLogOffsets(true);
            AppendLog("开始启动本地服务……");
            bool started = false;
            try
            {
                serviceProcess = runtime.StartServices(AppendLog);
                for (int i = 0; i < 55; i++)
                {
                    await Task.Delay(1000);
                    RuntimeStatus status = await runtime.GetStatusAsync();
                    if (status.IsRunning)
                    {
                        AppendLog("前端与后端均已就绪。");
                        started = true;
                        break;
                    }
                    if (serviceProcess.HasExited)
                    {
                        AppendLog("启动进程提前退出，退出码：" + serviceProcess.ExitCode);
                        break;
                    }
                }
                if (!started)
                {
                    AppendLog("启动未在预期时间内完成，请查看下方日志或运行环境体检。");
                }
            }
            catch (Exception ex)
            {
                AppendLog("启动失败：" + ex.Message);
            }
            finally
            {
                SetBusy(false, null);
            }
            await RefreshRuntimeStatus();
            if (started) SafeOpenWorkspace();
        }

        private async void StopButtonClick(object sender, EventArgs e)
        {
            await StopServices(false);
        }

        private async void RestartButtonClick(object sender, EventArgs e)
        {
            if (busy) return;
            await StopServices(true);
            StartButtonClick(sender, e);
        }

        private async Task StopServices(bool restarting)
        {
            if (busy) return;
            SetBusy(true, restarting ? "正在准备重启" : "正在停止服务");
            AppendLog(restarting ? "准备重新启动，先停止当前服务……" : "正在停止 OCV 本地服务……");
            try
            {
                await runtime.StopServicesAsync(AppendLog);
                await Task.Delay(700);
                RuntimeStatus stopped = await runtime.GetStatusAsync();
                if (stopped.BackendOnline || stopped.FrontendOnline)
                {
                    throw new InvalidOperationException("仍有本地服务未停止。请右键以管理员身份运行 OCV_Launcher.exe 后重试。");
                }
                AppendLog("停止操作已完成，相关端口均已释放。");
            }
            catch (Exception ex)
            {
                AppendLog("停止失败：" + ex.Message);
            }
            finally
            {
                if (serviceProcess != null) serviceProcess.Dispose();
                serviceProcess = null;
                SetBusy(false, null);
            }
            await RefreshRuntimeStatus();
        }

        private async void CheckButtonClick(object sender, EventArgs e)
        {
            if (busy) return;
            SetBusy(true, "正在检查环境");
            checkResults.Controls.Clear();
            environmentValue.Text = "检查中";
            environmentValue.ForeColor = Amber;
            AppendLog("开始运行便携环境体检……");
            try
            {
                List<CheckItem> items = await runtime.RunEnvironmentCheckAsync(AppendLog);
                int passed = 0;
                foreach (CheckItem item in items)
                {
                    if (item.Passed) passed++;
                    AddCheckResult(item);
                    AppendLog((item.Passed ? "[通过] " : "[失败] ") + item.Name + "：" + item.Detail);
                }
                bool allPassed = passed == items.Count;
                environmentValue.Text = allPassed ? "全部通过" : passed + "/" + items.Count + " 通过";
                environmentValue.ForeColor = allPassed ? Green : Red;
            }
            catch (Exception ex)
            {
                environmentValue.Text = "体检失败";
                environmentValue.ForeColor = Red;
                AppendLog("环境体检失败：" + ex.Message);
            }
            finally
            {
                SetBusy(false, null);
            }
        }

        private async Task RefreshRuntimeStatus()
        {
            RuntimeStatus status = await runtime.GetStatusAsync();
            backendValue.Text = status.BackendOnline ? "运行中" : "未运行";
            backendValue.ForeColor = status.BackendOnline ? Green : TextMuted;
            frontendValue.Text = status.FrontendOnline ? "运行中" : "未运行";
            frontendValue.ForeColor = status.FrontendOnline ? Green : TextMuted;

            if (status.IsRunning)
            {
                statusPill.Text = "●  OCV 正在运行";
                statusPill.ForeColor = Green;
                statusTitle.Text = "工作台已经就绪";
                statusDetail.Text = "点击“打开工作台”进入浏览器；关闭浏览器不会停止后台任务。";
                startButton.Text = "↗  打开 OCV 工作台";
                stopButton.Enabled = !busy;
                restartButton.Enabled = !busy;
            }
            else if (status.BackendOnline || status.FrontendOnline)
            {
                statusPill.Text = "●  服务不完整";
                statusPill.ForeColor = Amber;
                statusTitle.Text = "部分服务正在运行";
                statusDetail.Text = "建议点击“重新启动”，让前端和后端恢复到一致状态。";
                startButton.Text = "▶  补全并启动 OCV";
                stopButton.Enabled = !busy;
                restartButton.Enabled = !busy;
            }
            else
            {
                statusPill.Text = "○  OCV 未运行";
                statusPill.ForeColor = TextMuted;
                statusTitle.Text = "准备启动工作台";
                statusDetail.Text = "启动器会使用项目内的 Python、Node、FFmpeg、浏览器和模型，不依赖系统环境。";
                startButton.Text = "▶  启动 OCV";
                stopButton.Enabled = false;
                restartButton.Enabled = false;
            }
            startButton.Enabled = !busy;
        }

        private void SetBusy(bool value, string message)
        {
            busy = value;
            busyProgress.Visible = value;
            checkButton.Enabled = !value;
            startButton.Enabled = !value;
            if (value && !string.IsNullOrEmpty(message))
            {
                statusPill.Text = "●  " + message;
                statusPill.ForeColor = Amber;
            }
        }

        private void AddCheckResult(CheckItem item)
        {
            var label = NewLabel((item.Passed ? "✓  " : "×  ") + item.Name + " · " + item.Detail, 8F, FontStyle.Regular, item.Passed ? Green : Red);
            label.AutoEllipsis = true;
            label.Width = Math.Max(280, checkResults.ClientSize.Width - 28);
            label.Height = 25;
            label.Margin = new Padding(0, 1, 0, 1);
            checkResults.Controls.Add(label);
        }

        private void SafeOpenWorkspace()
        {
            try { runtime.OpenUrl(runtime.WorkspaceUrl); }
            catch (Exception ex) { AppendLog("打开工作台失败：" + ex.Message); }
        }

        private void AppendLog(string message)
        {
            if (string.IsNullOrWhiteSpace(message) || logBox == null || logBox.IsDisposed) return;
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(AppendLog), message);
                return;
            }
            string content = message.TrimEnd();
            const int maxMessageCharacters = 64000;
            if (content.Length > maxMessageCharacters)
            {
                content = "[日志内容过长，仅显示末尾部分]" + Environment.NewLine + content.Substring(content.Length - maxMessageCharacters);
            }
            string line = "[" + DateTime.Now.ToString("HH:mm:ss") + "] " + content + Environment.NewLine;
            logBox.AppendText(line);
            if (logBox.TextLength > 180000) logBox.Text = logBox.Text.Substring(logBox.TextLength - 130000);
            logBox.SelectionStart = logBox.TextLength;
            logBox.ScrollToCaret();
        }

        private string[] ServiceLogFiles()
        {
            return new[]
            {
                Path.Combine(runtime.LogsDirectory, "backend.stdout.log"),
                Path.Combine(runtime.LogsDirectory, "backend.stderr.log"),
                Path.Combine(runtime.LogsDirectory, "frontend.stdout.log"),
                Path.Combine(runtime.LogsDirectory, "frontend.stderr.log"),
                Path.Combine(runtime.LogsDirectory, "service_watchdog.log")
            };
        }

        private void InitializeLogOffsets(bool atEnd)
        {
            foreach (string path in ServiceLogFiles())
            {
                try { logOffsets[path] = atEnd && File.Exists(path) ? new FileInfo(path).Length : 0; }
                catch { logOffsets[path] = 0; }
            }
        }

        private void ReadNewServiceLogs()
        {
            foreach (string path in ServiceLogFiles())
            {
                try
                {
                    if (!File.Exists(path)) continue;
                    long offset;
                    if (!logOffsets.TryGetValue(path, out offset)) offset = 0;
                    long length = new FileInfo(path).Length;
                    if (length < offset) offset = 0;
                    if (length == offset) continue;
                    const long maxReadBytes = 65536;
                    if (length - offset > maxReadBytes)
                    {
                        offset = length - maxReadBytes;
                        AppendLog("[" + Path.GetFileName(path) + "] 日志增长过快，已跳过较早内容，仅显示最新 64 KiB。");
                    }
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    {
                        stream.Seek(offset, SeekOrigin.Begin);
                        using (var reader = new StreamReader(stream, Encoding.UTF8, true))
                        {
                            string text = reader.ReadToEnd();
                            logOffsets[path] = stream.Position;
                            if (!string.IsNullOrWhiteSpace(text)) AppendLog("[" + Path.GetFileName(path) + "] " + text.Trim());
                        }
                    }
                }
                catch { }
            }
        }

        private void LoadRecentLogs()
        {
            foreach (string path in ServiceLogFiles())
            {
                string tail = LauncherRuntime.TailFile(path, 28);
                if (!string.IsNullOrWhiteSpace(tail)) AppendLog("[最近日志 · " + Path.GetFileName(path) + "]" + Environment.NewLine + tail);
                try { logOffsets[path] = File.Exists(path) ? new FileInfo(path).Length : 0; } catch { }
            }
        }

        private async void MainFormClosing(object sender, FormClosingEventArgs e)
        {
            if (closingAfterPrompt) return;
            e.Cancel = true;
            RuntimeStatus status = await runtime.GetStatusAsync();
            if (!status.BackendOnline && !status.FrontendOnline)
            {
                closingAfterPrompt = true;
                Close();
                return;
            }

            DialogResult answer = MessageBox.Show(
                "OCV 服务仍在运行。\n\n选择“是”：停止服务并退出\n选择“否”：保留服务并退出\n选择“取消”：返回启动管理器",
                "退出 OCV 启动管理器",
                MessageBoxButtons.YesNoCancel,
                MessageBoxIcon.Question);
            if (answer == DialogResult.Cancel) return;
            if (answer == DialogResult.Yes) await StopServices(false);
            closingAfterPrompt = true;
            Close();
        }

        private static Panel CardPanel()
        {
            var panel = new Panel();
            panel.Dock = DockStyle.Fill;
            panel.BackColor = Panel;
            panel.BorderStyle = BorderStyle.FixedSingle;
            return panel;
        }

        private static Label NewLabel(string text, float size, FontStyle style, Color color)
        {
            var label = new Label();
            label.Text = text;
            label.ForeColor = color;
            label.BackColor = Color.Transparent;
            label.Font = new Font("Microsoft YaHei UI", size, style, GraphicsUnit.Point);
            label.Dock = DockStyle.Fill;
            label.TextAlign = ContentAlignment.MiddleLeft;
            return label;
        }

        private static Button AccentButton(string text, Color background, Color foreground)
        {
            var button = new Button();
            button.Text = text;
            button.Height = 46;
            button.Dock = DockStyle.Fill;
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = 0;
            button.BackColor = background;
            button.ForeColor = foreground;
            button.Cursor = Cursors.Hand;
            button.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
            return button;
        }

        private static Button SecondaryButton(string text)
        {
            var button = new Button();
            button.Text = text;
            button.AutoSize = true;
            button.Height = 38;
            button.Padding = new Padding(10, 4, 10, 4);
            button.Margin = new Padding(0, 0, 8, 6);
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderColor = Border;
            button.FlatAppearance.BorderSize = 1;
            button.BackColor = PanelSoft;
            button.ForeColor = TextMain;
            button.Cursor = Cursors.Hand;
            return button;
        }

        private static Button SmallButton(string text)
        {
            var button = SecondaryButton(text);
            button.Height = 30;
            button.Padding = new Padding(8, 1, 8, 1);
            button.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular);
            return button;
        }

        private static Label AddStatusRow(TableLayoutPanel parent, int row, string name, string initial)
        {
            var panel = new TableLayoutPanel();
            panel.Dock = DockStyle.Fill;
            panel.ColumnCount = 2;
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 60F));
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 40F));
            panel.BackColor = Color.Transparent;
            panel.Controls.Add(NewLabel(name, 9F, FontStyle.Regular, TextMuted), 0, 0);
            var value = NewLabel(initial, 9F, FontStyle.Bold, TextMuted);
            value.TextAlign = ContentAlignment.MiddleRight;
            panel.Controls.Add(value, 1, 0);
            parent.Controls.Add(panel, 0, row);
            return value;
        }

        private static Image LoadEmbeddedLogo()
        {
            try
            {
                Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("OcvLauncher.Logo.png");
                if (stream == null) return null;
                using (stream)
                using (Image source = Image.FromStream(stream))
                {
                    return new Bitmap(source);
                }
            }
            catch { return null; }
        }
    }
}
