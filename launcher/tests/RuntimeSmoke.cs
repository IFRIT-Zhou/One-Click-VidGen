using System;
using System.Collections.Generic;
using System.Threading;

namespace OcvLauncher
{
    internal static class RuntimeSmoke
    {
        private static int Main(string[] args)
        {
            try
            {
                var runtime = new LauncherRuntime();
                Console.WriteLine("ROOT=" + runtime.Root);
                Console.WriteLine("VERSION=" + runtime.VersionText);

                RuntimeStatus status = runtime.GetStatusAsync().GetAwaiter().GetResult();
                Console.WriteLine("BACKEND=" + status.BackendOnline);
                Console.WriteLine("FRONTEND=" + status.FrontendOnline);

                List<CheckItem> items = runtime.RunEnvironmentCheckAsync(Console.WriteLine).GetAwaiter().GetResult();
                int failed = 0;
                foreach (CheckItem item in items)
                {
                    Console.WriteLine((item.Passed ? "PASS " : "FAIL ") + item.Name + " :: " + item.Detail);
                    if (!item.Passed) failed++;
                }
                Console.WriteLine("CHECKS=" + items.Count + ";FAILED=" + failed);
                if (failed != 0) return 2;
                if (args.Length > 0 && string.Equals(args[0], "--lifecycle", StringComparison.OrdinalIgnoreCase))
                {
                    return RunLifecycle(runtime);
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.ToString());
                return 1;
            }
        }

        private static int RunLifecycle(LauncherRuntime runtime)
        {
            try
            {
                Console.WriteLine("LIFECYCLE=STARTING");
                System.Diagnostics.Process host = runtime.StartServices(Console.WriteLine);
                for (int i = 0; i < 60; i++)
                {
                    RuntimeStatus status = runtime.GetStatusAsync().GetAwaiter().GetResult();
                    if (status.IsRunning)
                    {
                        Console.WriteLine("LIFECYCLE=READY");
                        break;
                    }
                    if (host.HasExited)
                    {
                        Console.Error.WriteLine("Launcher host exited early: " + host.ExitCode);
                        return 3;
                    }
                    Thread.Sleep(1000);
                }
                RuntimeStatus ready = runtime.GetStatusAsync().GetAwaiter().GetResult();
                if (!ready.IsRunning)
                {
                    Console.Error.WriteLine("Services did not become ready.");
                    return 4;
                }
                return 0;
            }
            finally
            {
                runtime.StopServicesAsync(Console.WriteLine).GetAwaiter().GetResult();
                Thread.Sleep(700);
                RuntimeStatus stopped = runtime.GetStatusAsync().GetAwaiter().GetResult();
                Console.WriteLine("LIFECYCLE=" + ((!stopped.BackendOnline && !stopped.FrontendOnline) ? "STOPPED" : "STOP_FAILED"));
            }
        }
    }
}
