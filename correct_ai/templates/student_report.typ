#let data = json("data.json")

#set page(paper: "a4", margin: 1.5cm, footer: context [
  #align(right)[#text(fill: rgb("#6b7280"), size: 8pt)[Page #counter(page).display()]]
])
#set text(font: ("TeX Gyre Pagella", "DejaVu Sans"), size: 11pt, lang: "fr")
#set par(leading: 0.7em)
#set heading(numbering: none)

#let ink = rgb("#193b35")
#let panel(title, content, shade, rule) = block(
  width: 100%,
  fill: shade,
  stroke: (left: 2pt + rule),
  inset: (x: 10pt, y: 9pt),
  radius: 2pt,
  breakable: true,
)[
  #text(size: 8pt, weight: "bold", fill: rule)[#title]
  #v(4pt)
  #text(content)
]

#align(right)[#text(size: 8pt, fill: rgb("#54766d"))[KOREKTOR / BILAN INDIVIDUEL]]
#v(6pt)
#text(size: 21pt, weight: "bold", fill: ink)[Compte rendu de correction]
#v(14pt)

#block(width: 100%, fill: rgb("#f0f6f3"), inset: 13pt, radius: 3pt)[
  #grid(
    columns: (1fr, 1fr), gutter: 12pt,
    [*Nom* \ #text(data.nom)],
    [*Prénom* \ #text(data.prenom)],
    [*Note globale* \ #text(data.note_globale)],
    [*Moyenne de promotion* \ #text(data.moyenne_promotion)],
  )
]
#v(11pt)
#text(size: 8pt, fill: rgb("#61716d"))[#text(data.titre) · Copie #text(data.copie_id)]
#v(15pt)

#for question in data.questions {
  [
    #block(breakable: false)[
      #text(size: 13pt, weight: "bold", fill: ink)[#text(question.titre)]
      #h(1fr)
      #text(size: 10pt, weight: "bold", fill: ink)[#text(question.note)]
    ]
    #v(6pt)
    #panel("ÉNONCÉ", question.enonce, rgb("#eef3f6"), rgb("#356b8b"))
    #v(7pt)
    #panel("CORRIGÉ OFFICIEL", question.corrige, rgb("#edf8f1"), rgb("#207a50"))
    #v(7pt)
    #panel("APPRÉCIATION DU CORRECTEUR", question.appreciation, rgb("#fdf0ef"), rgb("#ba3835"))
    #if question.crop_image_path != "" {
      v(8pt)
      align(center)[
        #image(question.crop_image_path, width: 85%, height: 7cm, fit: "contain")
      ]
    }
    #v(13pt)
    #line(length: 100%, stroke: 0.5pt + rgb("#d8e3df"))
    #v(13pt)
  ]
}