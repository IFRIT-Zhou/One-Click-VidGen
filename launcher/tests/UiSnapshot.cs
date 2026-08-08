using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Threading;
using System.Windows.Forms;

namespace OcvLauncher
{
    internal static class UiSnapshot
    {
        [STAThread]
        private static int Main(string[] args)
        {
            string output = args.Length > 0 ? args[0] : "ui-preview.png";
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var form = new MainForm())
            {
                form.WindowState = FormWindowState.Normal;
                form.StartPosition = FormStartPosition.Manual;
                form.Location = new Point(-2000, -2000);
                form.Size = new Size(1002, 690);
                form.Show();
                Application.DoEvents();
                Thread.Sleep(700);
                Application.DoEvents();
                using (var bitmap = new Bitmap(form.ClientSize.Width, form.ClientSize.Height))
                {
                    form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                    bitmap.Save(output, ImageFormat.Png);
                }
                form.Dispose();
            }
            return 0;
        }
    }
}
