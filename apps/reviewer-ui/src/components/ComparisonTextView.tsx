"use client";

import type {
  ComparisonBlock,
  ComparisonItem,
  ComparisonStatus,
} from "@/lib/types";

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  both: "Ambos",
  opf_only: "Só OPF",
  regex_only: "Só Regex",
  partial_overlap: "Sobreposição parcial",
  type_conflict: "Conflito de tipo",
};

interface Highlight {
  start: number;
  end: number;
  status: ComparisonStatus;
  itemIndex: number;
}

// Each comparison item is rendered as a single highlight covering the
// union of its OPF and Regex spans. Items with no offsets at all (both
// sides null) are skipped — they cannot be located in the text.
function itemRange(item: ComparisonItem): { start: number; end: number } | null {
  const o = item.opf_span;
  const r = item.regex_span;
  if (o && r) {
    return { start: Math.min(o.start, r.start), end: Math.max(o.end, r.end) };
  }
  if (o) return { start: o.start, end: o.end };
  if (r) return { start: r.start, end: r.end };
  return null;
}

// Try to layer the items into non-overlapping highlights for one block.
// Returns null when offsets conflict (overlap or are out-of-bounds) so
// the caller can fall back to plain text without crashing.
function buildHighlights(
  text: string,
  itemsForBlock: Array<{ item: ComparisonItem; itemIndex: number }>
): Highlight[] | null {
  const highlights: Highlight[] = [];
  for (const { item, itemIndex } of itemsForBlock) {
    const range = itemRange(item);
    if (!range) continue;
    if (
      range.start < 0 ||
      range.end > text.length ||
      range.start >= range.end
    ) {
      return null; // out of bounds — bail out
    }
    highlights.push({
      start: range.start,
      end: range.end,
      status: item.status,
      itemIndex,
    });
  }
  highlights.sort((a, b) => a.start - b.start || a.end - b.end);

  // Detect mutual overlap: any pair where the next one starts before the
  // previous ends. Requirement: fall back to plain text if offsets conflict.
  for (let i = 1; i < highlights.length; i++) {
    if (highlights[i].start < highlights[i - 1].end) {
      return null;
    }
  }
  return highlights;
}

interface BlockProps {
  block: ComparisonBlock;
  highlights: Highlight[] | null;
  selected: number | null;
  onSelect: (itemIndex: number) => void;
}

function BlockText({ block, highlights, selected, onSelect }: BlockProps) {
  if (highlights === null) {
    return (
      <div className="cv-block">
        <div className="cv-block-id">{block.block_id}</div>
        <div className="cv-block-fallback muted small">
          ⚠ Offsets conflitantes neste bloco — exibindo texto sem
          marcação. Use a tabela abaixo para revisar.
        </div>
        <pre className="cv-block-text">{block.text}</pre>
      </div>
    );
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const h of highlights) {
    if (h.start > cursor) {
      parts.push(
        <span key={`t-${cursor}`}>{block.text.slice(cursor, h.start)}</span>
      );
    }
    const isSelected = selected === h.itemIndex;
    parts.push(
      <mark
        key={`h-${h.itemIndex}`}
        className={
          `cv-mark cv-mark-${h.status}` +
          (isSelected ? " cv-mark-selected" : "")
        }
        title={STATUS_LABEL[h.status]}
        onClick={() => onSelect(h.itemIndex)}
        // Make the mark keyboard-activatable for accessibility.
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(h.itemIndex);
          }
        }}
      >
        {block.text.slice(h.start, h.end)}
      </mark>
    );
    cursor = h.end;
  }
  if (cursor < block.text.length) {
    parts.push(<span key={`t-${cursor}`}>{block.text.slice(cursor)}</span>);
  }

  return (
    <div className="cv-block">
      <div className="cv-block-id">{block.block_id}</div>
      <pre className="cv-block-text">{parts}</pre>
    </div>
  );
}

interface Props {
  blocks: ComparisonBlock[];
  items: ComparisonItem[];
  selectedIndex: number | null;
  onSelect: (itemIndex: number) => void;
}

export function ComparisonTextView({
  blocks,
  items,
  selectedIndex,
  onSelect,
}: Props) {
  if (!blocks || blocks.length === 0) return null;

  // Group items by block_id, keeping the original index in `items` so
  // clicking a highlight can drive the table's expanded row.
  const itemsByBlock = new Map<
    string,
    Array<{ item: ComparisonItem; itemIndex: number }>
  >();
  items.forEach((item, itemIndex) => {
    const arr = itemsByBlock.get(item.block_id) ?? [];
    arr.push({ item, itemIndex });
    itemsByBlock.set(item.block_id, arr);
  });

  return (
    <div className="cv-wrap">
      <div className="cv-legend">
        <span className="muted small">Legenda:</span>
        <span className="cv-legend-item">
          <span className="cv-swatch cv-mark-both" /> Ambos
        </span>
        <span className="cv-legend-item">
          <span className="cv-swatch cv-mark-opf_only" /> Só OPF
        </span>
        <span className="cv-legend-item">
          <span className="cv-swatch cv-mark-regex_only" /> Só Regex
        </span>
        <span className="cv-legend-item">
          <span className="cv-swatch cv-mark-type_conflict" /> Conflito
        </span>
        <span className="cv-legend-item">
          <span className="cv-swatch cv-mark-partial_overlap" /> Parcial
        </span>
      </div>

      <div className="cv-blocks">
        {blocks.map((block) => {
          const blockItems = itemsByBlock.get(block.block_id) ?? [];
          const highlights = buildHighlights(block.text, blockItems);
          return (
            <BlockText
              key={block.block_id}
              block={block}
              highlights={highlights}
              selected={selectedIndex}
              onSelect={onSelect}
            />
          );
        })}
      </div>
    </div>
  );
}
