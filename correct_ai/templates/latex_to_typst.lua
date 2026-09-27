-- Pandoc Lua filter used by pdf_builder._markdown_to_typst.
--
-- The report pipeline first asks Pandoc to parse Markdown with the `raw_tex`
-- extension enabled, then writes Typst markup. Some answer statements contain
-- inline LaTeX commands that the Markdown parser preserves as RawInline nodes.
-- This filter translates a deliberately small, known subset of those nodes
-- through Pandoc's LaTeX reader so the normal Typst writer can serialize the
-- resulting semantic inline elements.
--
-- Scope and limitations:
--   * Only RawInline nodes whose format is exactly `tex` are considered.
--   * RawBlock nodes and general LaTeX documents are not handled here.
--   * The command allow-list is intentional; this is not a general TeX parser
--     or a guarantee that arbitrary LaTeX has a Typst equivalent.
--   * Unsupported TeX is downgraded to a Pandoc Str node. Its output is then
--     the responsibility of Pandoc's Typst writer, rather than this filter.

function RawInline(element)
  -- Returning nil leaves nodes in other raw formats unchanged for later
  -- filters or writers. In particular, this filter only owns raw TeX.
  if element.format ~= "tex" then
    return nil
  end

  -- Capture the leading alphabetic control sequence (for example, `emph` in
  -- `\emph{...}`). This is only a dispatch check: Pandoc's LaTeX reader below
  -- remains responsible for parsing the complete inline fragment and its
  -- arguments.
  local command = element.text:match("^\\([%a]+)")

  -- Keep this allow-list narrow and explicit. These commands occur in report
  -- content and are supported by Pandoc's LaTeX reader for inline conversion:
  --   emph      -> semantic emphasis, serialized by the Typst writer
  --   underline -> semantic underline, serialized by the Typst writer
  --   hfill     -> horizontal fill/spacing between inline content
  --   vspace    -> vertical spacing where represented by the reader
  local supported = {
    emph = true,
    hfill = true,
    underline = true,
    vspace = true,
  }

  -- Do not ask the LaTeX reader to interpret unknown commands. Returning a
  -- Str replaces the raw node with ordinary text and avoids handing arbitrary
  -- TeX syntax to the Typst writer as if it were Typst markup.
  if not command or not supported[command] then
    return pandoc.Str(element.text)
  end

  -- Parse the whole fragment, not just the command name. `pandoc.read` returns
  -- a Pandoc document whose blocks contain the inline AST (e.g. Emph and
  -- Str nodes); the selected inlines can then be rendered by Pandoc's Typst
  -- writer using its native syntax.
  local parsed = pandoc.read(element.text, "latex")
  local converted = {}

  -- RawInline content belongs in an inline list. Keep inline children from
  -- paragraph-like blocks and discard any other block nodes: they cannot be
  -- spliced into the surrounding inline position.
  for _, block in ipairs(parsed.blocks) do
    if block.t == "Para" or block.t == "Plain" then
      for _, inline in ipairs(block.content) do
        table.insert(converted, inline)
      end
    end
  end

  -- Returning a list replaces the original RawInline node with these parsed
  -- inlines. An empty list removes the original fragment entirely.
  return converted
end