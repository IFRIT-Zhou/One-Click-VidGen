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
                List<string> parsedSources = LauncherRuntime.ReadJsonStringArray(
                    "{\"channel_urls\":[\"https://download.example/update.json\",\"https://github.example/update.json\"]}",
                    "channel_urls");
                if (parsedSources.Count != 2 || !parsedSources[0].Contains("download.example"))
                {
                    Console.Error.WriteLine("Update-source JSON parser failed.");
                    return 6;
                }
                Console.WriteLine("UPDATE_SOURCE_PARSER=PASS");

                if (LauncherRuntime.ParseActiveTaskCount("{\"active_task_count\":2}") != 2
                    || LauncherRuntime.ParseActiveTaskCount("{\"ok\":true}") != -1)
                {
                    Console.Error.WriteLine("Active-task health parser failed.");
                    return 9;
                }
                Console.WriteLine("ACTIVE_TASK_PARSER=PASS");

                var runtime = new LauncherRuntime();
                Console.WriteLine("ROOT=" + runtime.Root);
                Console.WriteLine("VERSION=" + runtime.VersionText);

                if (args.Length > 0 && string.Equals(args[0], "--portable-update-check", StringComparison.OrdinalIgnoreCase))
                {
                    UpdateCheckResult portableUpdate = runtime.CheckForUpdatesAsync(Console.WriteLine).GetAwaiter().GetResult();
                    Console.WriteLine("UPDATE_MODE=" + portableUpdate.Mode);
                    Console.WriteLine("UPDATE_CAN_APPLY=" + portableUpdate.CanUpdate);
                    Console.WriteLine("UPDATE_CURRENT=" + portableUpdate.IsCurrent);
                    Console.WriteLine("UPDATE_BLOCKED=" + portableUpdate.IsBlocked);
                    Console.WriteLine("UPDATE_MESSAGE=" + portableUpdate.Message);
                    if (!string.Equals(portableUpdate.Mode, "portable", StringComparison.OrdinalIgnoreCase)) return 7;
                    if (string.IsNullOrWhiteSpace(portableUpdate.Message)) return 8;
                    return 0;
                }

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
                if (args.Length > 0 && string.Equals(args[0], "--update-check", StringComparison.OrdinalIgnoreCase))
                {
                    UpdateCheckResult update = runtime.CheckForUpdatesAsync(Console.WriteLine).GetAwaiter().GetResult();
                    Console.WriteLine("UPDATE_MODE=" + update.Mode);
                    Console.WriteLine("UPDATE_CAN_APPLY=" + update.CanUpdate);
                    Console.WriteLine("UPDATE_CURRENT=" + update.IsCurrent);
                    Console.WriteLine("UPDATE_BLOCKED=" + update.IsBlocked);
                    Console.WriteLine("UPDATE_MESSAGE=" + update.Message);
                    if (string.IsNullOrWhiteSpace(update.Mode) || string.IsNullOrWhiteSpace(update.Message)) return 5;
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
