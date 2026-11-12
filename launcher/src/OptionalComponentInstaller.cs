using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace OcvLauncher
{
    // All writes are to a new staging directory. Existing installations are never replaced.
    internal static class OptionalComponentInstaller
    {
        internal const long WeightBytes = 884878856;
        internal const string WeightSha = "77277264c0e8c74149d3ff2fade4fd8176965b7108f3c5fc3b8c9c811edb4519";
        internal static string WeightPath(string root)
        {
            return Path.Combine(root, "tools", "rmbg", "models", "BiRefNet", "BiRefNet-general.safetensors");
        }
        internal static void RejectLinks(string path)
        {
            for (var current = new DirectoryInfo(Path.GetFullPath(path)); current != null; current = current.Parent)
                if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("目标含目录链接，拒绝写入：" + current.FullName);
        }
        internal static void VerifyFile(string path, long size, string hash)
        {
            if (new FileInfo(path).Length != size) throw new IOException("文件大小与支持版本不符，原文件保持不变。");
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                if (!string.Equals(BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", ""), hash, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("SHA-256 校验不符，原文件保持不变。");
        }
        internal static string ImportVerified(string source, string target, long size, string hash)
        {
            RejectLinks(Path.GetDirectoryName(target));
            if (File.Exists(target))
            {
                VerifyFile(target, size, hash);
                return "现有文件校验通过，无需导入；没有覆盖任何文件。";
            }
            VerifyFile(source, size, hash);
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            string stage = target + "." + Guid.NewGuid().ToString("N") + ".partial";
            File.Copy(source, stage, false);
            VerifyFile(stage, size, hash);
            // Move refuses an existing destination, including one created during the copy.
            File.Move(stage, target);
            return "已校验并导入。源文件保留，已有文件未覆盖。";
        }
        internal static void ValidateRegistry(string text)
        {
            var matches = Regex.Matches(text, "\"resolved\"\\s*:\\s*\"([^\"]+)\"");
            if (matches.Count == 0) throw new IOException("依赖锁定清单没有可校验的下载地址。");
            foreach (Match match in matches)
            {
                Uri uri;
                if (!Uri.TryCreate(match.Groups[1].Value, UriKind.Absolute, out uri)
                    || uri.Scheme != "https" || uri.Host != "registry.npmjs.org" || !uri.IsDefaultPort || uri.UserInfo.Length != 0)
                    throw new IOException("依赖清单包含非官方 npm 下载源；未开始安装。");
            }
            if (Regex.IsMatch(text, "\"link\"\\s*:\\s*true"))
                throw new IOException("不支持链接式依赖。");
        }
        internal static string InstallRemotion(string root, Action<string> log)
        {
            string renderer = Path.Combine(root, "tools", "vibe_remotion");
            string destination = Path.Combine(renderer, "node_modules");
            RejectLinks(renderer);
            if (Directory.Exists(destination) || File.Exists(destination))
                return "检测到已有 Remotion 依赖目录。本次不覆盖、不删除；如有故障请先诊断原环境。";
            string node = Path.Combine(root, "runtime", "node", "node.exe");
            string npm = Path.Combine(root, "runtime", "node", "node_modules", "npm", "bin", "npm-cli.js");
            if (!File.Exists(node) || !File.Exists(npm)) throw new IOException("缺少 OCV 便携 Node/npm，请先修复基础运行环境。");
            string lockText = File.ReadAllText(Path.Combine(renderer, "package-lock.json"));
            ValidateRegistry(lockText);
            string stage = Path.Combine(renderer, ".cache", "optional-install-" + Guid.NewGuid().ToString("N"));
            RejectLinks(stage);
            Directory.CreateDirectory(stage);
            File.Copy(Path.Combine(renderer, "package.json"), Path.Combine(stage, "package.json"));
            File.WriteAllText(Path.Combine(stage, "package-lock.json"), lockText, new UTF8Encoding(false));
            File.WriteAllText(Path.Combine(stage, "license-consent.txt"), "User confirmed Remotion license eligibility before installation. UTC=" + DateTime.UtcNow.ToString("o"));
            log("安装到独立暂存目录；锁定版本、使用 npm 官方源和包完整性校验，不执行包安装脚本。");
            Run(node, "\"" + npm + "\" ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org --cache=\"" + Path.Combine(stage, "npm-cache") + "\"", stage, log);
            Run(node, "-e \"for(const p of ['remotion','@remotion/renderer','@remotion/bundler']){require(p);console.log(p+' OK')}\"", stage, log);
            RejectLinks(renderer);
            Directory.Move(Path.Combine(stage, "node_modules"), destination);
            return "Remotion 依赖安装并加载检查通过。没有更改现有组件或重启引擎；浏览器运行依赖仍以环境检查为准。";
        }
        private static void Run(string exe, string arguments, string directory, Action<string> log)
        {
            var info = new ProcessStartInfo(exe, arguments) { WorkingDirectory = directory, UseShellExecute = false,
                CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            info.EnvironmentVariables["PATH"] = Path.GetDirectoryName(exe) + ";" + info.EnvironmentVariables["PATH"];
            info.EnvironmentVariables["NPM_CONFIG_USERCONFIG"] = Path.Combine(directory, "empty.npmrc");
            info.EnvironmentVariables["NPM_CONFIG_GLOBALCONFIG"] = Path.Combine(directory, "empty-global.npmrc");
            using (var process = new Process { StartInfo = info })
            {
                process.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) log(e.Data); };
                process.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) log(e.Data); };
                process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
                if (!process.WaitForExit(20 * 60 * 1000))
                {
                    process.Kill(); process.WaitForExit();
                    throw new IOException("安装等待超时。暂存文件保留，原环境不变；未自动重试。");
                }
                process.WaitForExit();
                if (process.ExitCode != 0) throw new IOException("依赖安装或加载检查失败，暂存记录保留，原环境不变。");
            }
        }
    }
}
