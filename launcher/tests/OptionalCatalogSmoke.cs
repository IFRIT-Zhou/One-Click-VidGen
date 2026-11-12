using System;
using System.IO;
namespace OcvLauncher
{
    internal static class OptionalCatalogSmoke
    {
        private static void Reject(string value)
        {
            try { OptionalComponentCatalog.Parse(value); }
            catch (Exception) { Console.WriteLine("PASS invalid catalog rejected"); return; }
            throw new Exception("Invalid catalog accepted");
        }
        private static int Main(string[] args)
        {
            string json = File.ReadAllText(args[0]);
            var list = OptionalComponentCatalog.Parse(json);
            if (list.Count != 7) throw new Exception("Catalog count");
            Console.WriteLine("PASS current catalog");
            var changed = OptionalComponentCatalog.Parse(json.Replace("Vibe 动效", "新增功能分类"));
            if (changed[0].Category != "新增功能分类") throw new Exception("Category not dynamic");
            Console.WriteLine("PASS category changes without recompilation");
            Reject(json.Replace("\"schema\": 1", "\"schema\": 99"));
            Reject(json.Replace("rmbg_runtime", "run_shell"));
            Reject(json.Replace("\"schema\": 1", "\"command\":\"powershell\",\"schema\":1"));
            Reject(json.Replace("https://github.com", "file:///C:/"));
            Reject(json.Replace("https://github.com", "http://github.com"));
            Reject(json.Replace("birefnet-weight", "rmbg-runtime"));
            Reject("{}");
            Reject("{not-json}");
            return 0;
        }
    }
}
