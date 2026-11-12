using System;
using System.IO;
using System.Security.Cryptography;

namespace OcvLauncher
{
    internal static class OptionalComponentsSmoke
    {
        private static void Assert(bool value, string name) { if (!value) throw new Exception(name); Console.WriteLine("PASS " + name); }
        private static void Refuses(Action action, string name)
        {
            try { action(); } catch (IOException) { Console.WriteLine("PASS " + name); return; }
            throw new Exception("Expected refusal: " + name);
        }
        private static int Main(string[] args)
        {
            // The fixture is intentionally retained: this suite performs no deletions.
            string root = Path.Combine(args[0], "optional-smoke-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string src = Path.Combine(root, "source.bin"), dst = Path.Combine(root, "installed.bin");
            File.WriteAllText(src, "fixture");
            string hash;
            using (var sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(src))).Replace("-", "");
            OptionalComponentInstaller.ImportVerified(src, dst, 7, hash);
            Assert(File.ReadAllText(src) == "fixture" && File.ReadAllText(dst) == "fixture", "import retains source");
            File.WriteAllText(src, "changed");
            OptionalComponentInstaller.ImportVerified(src, dst, 7, hash);
            Assert(File.ReadAllText(dst) == "fixture", "existing valid target never overwritten");
            File.WriteAllText(dst, "custom local content");
            Refuses(delegate { OptionalComponentInstaller.ImportVerified(src, dst, 7, hash); }, "existing different target refused");
            Assert(File.ReadAllText(dst) == "custom local content", "existing custom file retained");
            Refuses(delegate { OptionalComponentInstaller.ImportVerified(src, Path.Combine(root, "bad.bin"), 7, hash); }, "wrong checksum refused");
            Assert(!File.Exists(Path.Combine(root, "bad.bin")), "bad file not activated");
            OptionalComponentInstaller.ValidateRegistry("{\"resolved\":\"https://registry.npmjs.org/a/-/a.tgz\"}");
            if (args.Length > 1) { OptionalComponentInstaller.ValidateRegistry(File.ReadAllText(args[1])); Console.WriteLine("PASS real pinned registry manifest"); }
            Refuses(delegate { OptionalComponentInstaller.ValidateRegistry("{\"resolved\":\"https://registry.npmjs.org.evil/a\"}"); }, "unofficial registry refused");
            Refuses(delegate { OptionalComponentInstaller.ValidateRegistry("{\"resolved\":\"http://registry.npmjs.org/a\"}"); }, "HTTP refused");
            Refuses(delegate { OptionalComponentInstaller.ValidateRegistry("{\"resolved\":\"file:///tmp/a\"}"); }, "local resolved dependencies refused");
            string existing = Path.Combine(root, "tools", "vibe_remotion", "node_modules");
            Directory.CreateDirectory(existing);
            File.WriteAllText(Path.Combine(existing, "user.txt"), "keep");
            OptionalComponentInstaller.InstallRemotion(root, Console.WriteLine);
            Assert(File.ReadAllText(Path.Combine(existing, "user.txt")) == "keep", "existing renderer skips network and installation");
            Console.WriteLine("FIXTURE=" + root);
            return 0;
        }
    }
}
