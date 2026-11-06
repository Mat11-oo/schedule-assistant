import { h } from "./core.js?v=__APP_VERSION__";
import { renderMarkdown } from "./markdown.js?v=__APP_VERSION__";

// Read-only presentation. Source text and task state are never changed here.
export function renderDocument(value) {
  const article = h("article", { class: "document-prose", "aria-label": "文档正文" });
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const rich = (tag, text) => {
    const node = h(tag);
    node.innerHTML = renderMarkdown(text);
    return node;
  };
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (!line.trim()) continue;
    if (/^```/.test(line)) {
      const code = [];
      while (++index < lines.length && !/^```/.test(lines[index])) code.push(lines[index]);
      article.append(h("pre", {}, [h("code", { text: code.join("\n") })]));
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { article.append(rich(`h${Math.min(heading[1].length + 1, 6)}`, heading[2])); continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { article.append(h("hr")); continue; }
    const check = line.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/);
    if (check) {
      article.append(h("div", { class: "document-check" }, [
        h("input", { type: "checkbox", checked: check[1].toLowerCase() === "x", disabled: true, "aria-label": check[2] }), rich("div", check[2]),
      ]));
      continue;
    }
    const block = [line];
    while (index + 1 < lines.length && lines[index + 1].trim()
      && !/^(?:#{1,6}\s|```|\s*[-*+]\s+\[[ xX]\]\s|\s*(?:---+|\*\*\*+)\s*$)/.test(lines[index + 1])) block.push(lines[++index]);
    article.append(rich("div", block.join("\n")));
  }
  return article;
}
