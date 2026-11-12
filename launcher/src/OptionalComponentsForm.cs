using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace OcvLauncher
{
    internal sealed class OptionalComponentsForm : Form
    {
        private bool installing;
        private readonly TextBox output = new TextBox { Dock = DockStyle.Bottom, Height = 100, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical };
        public OptionalComponentsForm(string root, Action installRmbg)
        {
            Text = "可选组件与模型";
            Size = new Size(800, 540);
            MinimumSize = new Size(650, 440);
            StartPosition = FormStartPosition.CenterParent;
            Font = new Font("Microsoft YaHei UI", 10F);
            var tabs = new TabControl { Dock = DockStyle.Fill };
            Controls.Add(tabs);
            Controls.Add(output);
            FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (installing) { e.Cancel = true; MessageBox.Show(this, "正在安装或校验，请等待完成；不会重启引擎。", "组件管理"); }
            };
            try
            {
                var pages = new Dictionary<string, FlowLayoutPanel>();
                foreach (var entry in OptionalComponentCatalog.Load(Path.Combine(root, "launcher", "optional-components.json")))
                {
                    FlowLayoutPanel page;
                    if (!pages.TryGetValue(entry.Category, out page)) { page = Page(tabs, entry.Category); pages.Add(entry.Category, page); }
                    var selected = entry;
                    Item(page, entry.Title, entry.Description, entry.Label, delegate { Execute(selected, root, installRmbg); });
                }
            }
            catch (Exception ex) { Log("组件清单不可用：" + ex.Message + " 请通过安全更新修复；本机组件未改动。"); }
            var policy = Page(tabs, "安装与许可说明");
            policy.Controls.Add(new Label { AutoSize = true, MaximumSize = new Size(700, 0), Text = "组件按需安装不代表自动取得授权。OCV负责自身集成及发布义务，用户需确认自己的使用资格。\n\n组件清单随 OCV 安全更新发布，并纳入内容校验。普通分类、组件资料和已支持的安装入口不需要重新编译启动器；新安装方式仍需升级启动器。\n\n清单不能执行自定义命令、脚本或任意修改安装目录。现有文件不覆盖，不会重启引擎。" });
        }
        private void Execute(OptionalComponentEntry entry, string root, Action installRmbg)
        {
            if (entry.Action == "link") { Open(entry.Url); return; }
            if (entry.Action == "h3_docs") { Open(Path.Combine(root, "components", "comfyui")); return; }
            if (entry.Action == "rmbg_runtime") { Close(); installRmbg(); return; }
            if (entry.Action == "birefnet_import") {
                string target = OptionalComponentInstaller.WeightPath(root);
                if (File.Exists(target)) { Work(delegate { OptionalComponentInstaller.VerifyFile(target, OptionalComponentInstaller.WeightBytes, OptionalComponentInstaller.WeightSha); return "现有抠图权重校验通过，未修改。"; }); return; }
                using (var pick = new OpenFileDialog { Filter = "模型权重|*.safetensors", Title = "选择已下载的 BiRefNet-general 权重" })
                    if (pick.ShowDialog(this) == DialogResult.OK) { string source = pick.FileName; Work(delegate { return OptionalComponentInstaller.ImportVerified(source, target, OptionalComponentInstaller.WeightBytes, OptionalComponentInstaller.WeightSha); }); }
                return;
            }
            if (entry.Action == "remotion_install") {
                if (Directory.Exists(Path.Combine(root, "tools", "vibe_remotion", "node_modules"))) { MessageBox.Show(this, "已有依赖目录，本次不覆盖或删除。如有问题需另行诊断。", "保留本机安装"); return; }
                if (MessageBox.Show(this, "请先查看上方官方许可链接。\n\n是否确认你的使用符合 Remotion 免费许可，或已取得所需授权，并同意从 npm 官方源下载锁定依赖？\n\n此确认不免除 OCV 自身的许可义务。", "安装许可确认", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) == DialogResult.Yes)
                    Work(delegate { return OptionalComponentInstaller.InstallRemotion(root, Log); });
                return;
            }
            throw new InvalidOperationException("不支持的安装动作，请更新 Launcher。");
        }
        private void Log(string message)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string>(Log), message); return; }
            output.AppendText(message + Environment.NewLine);
        }
        private async void Work(Func<string> operation)
        {
            if (installing) return;
            installing = true;
            foreach (Control child in Controls) if (child is TabControl) child.Enabled = false;
            try { Log("开始检查 / 安装；已有组件保留。"); Log(await Task.Run(operation)); }
            catch (Exception ex) { Log("未完成：" + ex.Message); }
            finally { installing = false; foreach (Control child in Controls) if (child is TabControl) child.Enabled = true; }
        }
        private static FlowLayoutPanel Page(TabControl tabs, string title)
        {
            var page = new TabPage(title);
            var flow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoScroll = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(16) };
            flow.SizeChanged += delegate {
                int width = Math.Max(200, flow.ClientSize.Width - flow.Padding.Horizontal - SystemInformation.VerticalScrollBarWidth - 8);
                foreach (Control child in flow.Controls)
                    if (child is Label) child.MaximumSize = new Size(width, 0);
            };
            page.Controls.Add(flow); tabs.TabPages.Add(page); return flow;
        }
        private static void Item(FlowLayoutPanel parent, string title, string description, string action, Action callback)
        {
            parent.Controls.Add(new Label { Text = title, AutoSize = true, Font = new Font(parent.Font, FontStyle.Bold), Margin = new Padding(0, 12, 0, 4) });
            parent.Controls.Add(new Label { Text = description, AutoSize = true, MaximumSize = new Size(690, 0) });
            var button = new Button { Text = action, AutoSize = true, Padding = new Padding(8, 3, 8, 3), Margin = new Padding(0, 6, 0, 14) };
            button.Click += delegate { try { callback(); } catch (Exception ex) { MessageBox.Show(parent.FindForm(), ex.Message, "组件管理"); } };
            parent.Controls.Add(button);
        }
        private static void Open(string target) { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
    }
}
