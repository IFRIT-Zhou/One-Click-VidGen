using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace OcvLauncher
{
    internal sealed class OptionalComponentEntry
    {
        public string Id, Category, Title, Description, Action, Label, Url;
    }
    internal static class OptionalComponentCatalog
    {
        internal static List<OptionalComponentEntry> Load(string path)
        {
            return Parse(File.ReadAllText(path));
        }
        internal static List<OptionalComponentEntry> Parse(string json)
        {
            var serializer = new JavaScriptSerializer { MaxJsonLength = 262144, RecursionLimit = 12 };
            var root = serializer.DeserializeObject(json) as Dictionary<string, object>;
            Keys(root, "schema", "components");
            if (!root.ContainsKey("schema") || !object.Equals(root["schema"], 1))
                throw new InvalidDataException("组件清单版本不受支持，请先更新 Launcher。");
            var entries = root.ContainsKey("components") ? root["components"] as object[] : null;
            if (entries == null || entries.Length > 100) throw new InvalidDataException("组件列表无效。");
            var result = new List<OptionalComponentEntry>();
            var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var value in entries)
            {
                var row = value as Dictionary<string, object>;
                Keys(row, "id", "category", "title", "description", "action", "label", "url");
                var item = new OptionalComponentEntry { Id = Text(row, "id", 80), Category = Text(row, "category", 40),
                    Title = Text(row, "title", 160), Description = Text(row, "description", 2000),
                    Action = Text(row, "action", 40), Label = Text(row, "label", 80),
                    Url = row.ContainsKey("url") ? Text(row, "url", 1000) : null };
                if (!Regex.IsMatch(item.Id, "^[a-z0-9][a-z0-9_-]*$") || !ids.Add(item.Id))
                    throw new InvalidDataException("组件 ID 无效或重复。");
                if (item.Action != "link" && item.Action != "rmbg_runtime" && item.Action != "birefnet_import"
                    && item.Action != "remotion_install" && item.Action != "h3_docs")
                    throw new InvalidDataException("未知安装方式，请先更新 Launcher：" + item.Action);
                if (item.Url != null)
                {
                    Uri uri;
                    if (!Uri.TryCreate(item.Url, UriKind.Absolute, out uri) || uri.Scheme != "https"
                        || uri.UserInfo.Length != 0 || !uri.IsDefaultPort || uri.HostNameType != UriHostNameType.Dns)
                        throw new InvalidDataException("组件链接必须是 HTTPS 网站地址。");
                }
                if (item.Action == "link" && item.Url == null) throw new InvalidDataException("缺少组件链接。");
                result.Add(item);
            }
            return result;
        }
        private static string Text(Dictionary<string, object> row, string key, int max)
        {
            object value;
            var text = row.TryGetValue(key, out value) ? value as string : null;
            if (string.IsNullOrWhiteSpace(text) || text.Length > max) throw new InvalidDataException("组件字段无效：" + key);
            return text;
        }
        private static void Keys(Dictionary<string, object> row, params string[] allowed)
        {
            if (row == null) throw new InvalidDataException("组件清单结构无效。");
            foreach (var key in row.Keys)
                if (Array.IndexOf(allowed, key) < 0) throw new InvalidDataException("不支持的组件字段：" + key);
        }
    }
}
