// Template for generating student correction reports in Typst
// This template generates a detailed correction report for a student's work, including individual question feedback, grades, and teacher comments.

#let default-data = (
  copie_id: "—",
  nom: "Non renseigné",
  prenom: "Non renseigné",
  note_globale: "Non notée",
  moyenne_promotion: "Non disponible",
  note_sur_20: "Non disponible",
  moyenne_sur_20: "Non disponible",
  titre: "Aucune donnée de rapport fournie",
  questions: (),
)
#let data-path = sys.inputs.at("report-data-path", default: none)
#let data = if data-path == none { default-data } else { json(data-path) }

#set page(
  paper: "a4",
  margin: 1.8cm,
  header: context [
    #grid(
      columns: (1fr, auto),
      column-gutter: 12pt,
      text(fill: rgb("#54766d"), size: 9pt)[Bilan individuel de correction : #data.nom #data.prenom],
      text(fill: rgb("#54766d"), size: 9pt)[#datetime.today().display("[day]/[month]/[year]")],
    )
  ],
  footer: context [
    #align(right)[
      #text(fill: rgb("#6b7280"), size: 9pt)[#counter(page).display("1 / 1", both: true)]
    ]
  ],
)

#set text(font: ("TeX Gyre Pagella", "DejaVu Sans"), size: 11pt, lang: "fr")
#set par(leading: 0.7em)
#set heading(numbering: none)

#let ink = rgb("#193b35")
#let quote-block(content) = block(
  width: 100%,
  stroke: (left: 1.5pt + rgb("#8ca49d")),
  inset: (left: 8pt, y: 3pt),
)[#content]
#let horizontal-rule = line(length: 100%, stroke: 0.5pt + rgb("#b8c8c3"))
#let rich-text(source) = eval(
  source,
  mode: "markup",
  scope: (
    blockquote: quote-block,
    horizontalrule: horizontal-rule,
  ),
)
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
  #content
]

#text(size: 21pt, weight: "bold", fill: ink)[Compte rendu de correction]
#v(14pt)

#block(width: 100%, fill: rgb("#f0f6f3"), inset: 13pt, radius: 3pt)[
  #grid(
    columns: (1fr, 1fr), gutter: 12pt,
    [*Nom :* #text(data.nom)],
    [*Prénom :* #text(data.prenom)],
    [*Note globale :* #text(data.note_globale)],
    [*Moyenne de classe :* #text(data.moyenne_promotion)],
    [*Note sur 20 :* #text(data.note_sur_20)],
    [*Moyenne sur 20 :* #text(data.moyenne_sur_20)],
  )
]
#v(11pt)
#text(size: 8pt, fill: rgb("#61716d"))[#text(data.titre) · Copie #text(data.copie_id)]
#v(15pt)

#for question in data.questions {
  [
    #for title in question.section_titles {
      text(size: 15pt, weight: "bold", fill: ink)[#rich-text(title)]
      v(8pt)
    }
    #block(breakable: false)[
      #text(size: 13pt, weight: "bold", fill: ink)[#text(question.titre)]
      #h(1fr)
      #text(size: 15pt, weight: "bold", fill: ink)[#text(question.note)]
    ]
    #v(6pt)
    #panel("Énoncé", rich-text(question.enonce), rgb("#eef3f6"), rgb("#356b8b"))
    #v(7pt)
    #panel("Corrigé", rich-text(question.corrige), rgb("#edf8f1"), rgb("#207a50"))
    #v(7pt)
    #panel("Appréciation du correcteur", question.appreciation, rgb("#fdf0ef"), rgb("#ba3835"))
    #if question.crop_image_path != "" {
      v(8pt)
      align(center)[
        #image(question.crop_image_path, width: 85%)
      ]
    }
    #v(13pt)
    #line(length: 100%, stroke: 0.5pt + rgb("#d8e3df"))
    #v(13pt)
  ]
}