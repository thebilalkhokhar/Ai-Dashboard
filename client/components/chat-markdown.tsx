"use client";

// A deliberately small markdown renderer for assistant answers.
//
// The chat is the only surface that shows model prose, and the assistant is
// instructed to answer in a short lead line plus a few bullet lines, so this
// covers that subset - paragraphs, headings, lists, tables, blockquotes, rules,
// fenced code and inline bold/italic/code/links - instead of pulling a parser
// dependency into the app for it.
//
// Every node is built from plain strings through React elements: no HTML is ever
// parsed or injected, so a model-produced `<img onerror=...>` or a stray tag
// renders as the literal text it is.

import { Fragment, type ReactNode } from "react";

const INLINE_RE = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g;

// Only the schemes a business answer can legitimately contain. Anything else is
// rendered as the text the model wrote, not as a link.
function safeHref(raw: string): string | null {
  const href = raw.trim();
  return /^(https?:|mailto:)/i.test(href) ? href : null;
}

function inlineNodes(text: string, keyBase: string): ReactNode[] {
  const parts = text.split(INLINE_RE).filter((part) => part !== "" && part !== undefined);
  return parts.map((part, index) => {
    const key = `${keyBase}-${index}`;
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      const href = safeHref(link[2]);
      if (!href) return <Fragment key={key}>{part}</Fragment>;
      return (
        <a key={key} href={href} target="_blank" rel="noreferrer noopener" className="cmd-link">
          {link[1]}
        </a>
      );
    }
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (/^[_][^_]+[_]$/.test(part) || /^\*[^*]+\*$/.test(part)) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    if (/^`[^`]+`$/.test(part)) return <code key={key}>{part.slice(1, -1)}</code>;
    return <Fragment key={key}>{part}</Fragment>;
  });
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

const isRule = (line: string) => /^ {0,3}([-*_])\s*(?:\1\s*){2,}$/.test(line);
const isHeading = (line: string) => /^ {0,3}#{1,4}\s+/.test(line);
const isBullet = (line: string) => /^ {0,3}[-*+]\s+/.test(line);
const isOrdered = (line: string) => /^ {0,3}\d+[.)]\s+/.test(line);
const isQuote = (line: string) => /^ {0,3}>\s?/.test(line);
const isTableSeparator = (line: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes("-");

export function ChatMarkdown({ text }: { text: string }) {
  const lines = (text ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  const push = (node: ReactNode) => blocks.push(<Fragment key={blocks.length}>{node}</Fragment>);

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    // Fenced code: kept verbatim, never markdown-parsed inside.
    const fence = /^```([^\n]*)$/.exec(line.trim());
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index].trim())) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      push(
        <pre className="cmd-pre">
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    if (isRule(line)) {
      push(<hr className="cmd-hr" />);
      index += 1;
      continue;
    }

    const heading = /^( {0,3})(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(6, heading[2].length + 2);
      const Tag = (`h${level}` as unknown) as "h3";
      push(<Tag className="cmd-h">{inlineNodes(heading[3].trim(), `h${index}`)}</Tag>);
      index += 1;
      continue;
    }

    // Table: a header row, a |---| separator, then body rows.
    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const header = splitRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      push(
        <div className="cmd-table-wrap">
          <table className="cmd-table">
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th key={cellIndex} scope="col">
                    {inlineNodes(cell, `th${index}-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {header.map((_, cellIndex) => (
                    <td key={cellIndex}>{inlineNodes(row[cellIndex] ?? "", `td${rowIndex}-${cellIndex}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (isQuote(line)) {
      const body: string[] = [];
      while (index < lines.length && isQuote(lines[index])) {
        body.push(lines[index].replace(/^ {0,3}>\s?/, ""));
        index += 1;
      }
      push(<blockquote className="cmd-quote">{inlineNodes(body.join(" "), `q${index}`)}</blockquote>);
      continue;
    }

    if (isBullet(line) || isOrdered(line)) {
      const ordered = isOrdered(line);
      const items: string[] = [];
      while (index < lines.length && (ordered ? isOrdered(lines[index]) : isBullet(lines[index]))) {
        items.push(lines[index].replace(/^ {0,3}(?:[-*+]|\d+[.)])\s+/, ""));
        index += 1;
      }
      const ListTag = ordered ? "ol" : "ul";
      push(
        <ListTag className={ordered ? "cmd-list cmd-list--ordered" : "cmd-list"}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{inlineNodes(item, `li${index}-${itemIndex}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    // Paragraph: consecutive plain lines become one block.
    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isRule(lines[index]) &&
      !isHeading(lines[index]) &&
      !isBullet(lines[index]) &&
      !isOrdered(lines[index]) &&
      !isQuote(lines[index]) &&
      !/^```/.test(lines[index].trim())
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    push(
      <p className="cmd-p">
        {paragraph.map((part, partIndex) => (
          <Fragment key={partIndex}>
            {partIndex > 0 && <br />}
            {inlineNodes(part, `p${index}-${partIndex}`)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <div className="cmd">{blocks}</div>;
}
