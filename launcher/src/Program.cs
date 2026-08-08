using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace OcvLauncher
{
    internal static class Program
    {
        private const string MutexName = "OneClickVidGen.OcvLauncher.SingleInstance";

        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        private static void Main()
        {
            try { SetProcessDPIAware(); } catch { }

            bool createdNew;
            using (var mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    MessageBox.Show(
                        "OCV 启动管理器已经在运行。",
                        "OCV Launcher",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm());
            }
        }
    }
}
