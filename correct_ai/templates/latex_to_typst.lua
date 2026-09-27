function RawInline(element)
  if element.format ~= "tex" then
    return nil
  end

  local command = element.text:match("^\\([%a]+)")
  local supported = {
    emph = true,
    hfill = true,
    underline = true,
    vspace = true,
  }
  if not command or not supported[command] then
    return pandoc.Str(element.text)
  end

  local parsed = pandoc.read(element.text, "latex")
  local converted = {}
  for _, block in ipairs(parsed.blocks) do
    if block.t == "Para" or block.t == "Plain" then
      for _, inline in ipairs(block.content) do
        table.insert(converted, inline)
      end
    end
  end
  return converted
end