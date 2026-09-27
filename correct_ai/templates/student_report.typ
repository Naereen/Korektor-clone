// Template for generating student correction reports in Typst
// This template generates a detailed correction report for a student's work, including individual question feedback, grades, and teacher comments.

#import "vendor/cmarker/lib.typ": render-string

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
#let get-field(object, key, fallback) = {
  let value = object.at(key, default: fallback)
  if value == none { fallback } else { value }
}

#set page(
  paper: "a4",
  margin: 1.8cm,
  header: context [
    #grid(
      columns: (1fr, auto),
      column-gutter: 12pt,
      text(fill: rgb("#54766d"), size: 9pt)[Bilan individuel de correction : #get-field(data, "nom", default-data.nom) #get-field(data, "prenom", default-data.prenom)],
      text(fill: rgb("#54766d"), size: 9pt)[#datetime.today().display("[day]/[month]/[year]")],
    )
  ],
  footer: context [
    #align(center)[
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

#let rich-text-noreponse(source) = eval(
  source.split("\n")
    .filter(line => not (
      "**Réponse :**", "**Réponse**", "#strong[Réponse :]", "#strong[Réponse]",
    ).contains(line.trim()))
    .join("\n"),
  mode: "markup",
  scope: (
    blockquote: quote-block,
    horizontalrule: horizontal-rule,
  ),
)

#let rich-text-comment(source) = render-string(
  source,
  blockquote: quote-block,
  scope: (horizontalrule: horizontal-rule,),
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

#text(size: 17pt, fill: rgb("#61716d"))[#text(get-field(data, "titre", default-data.titre)) - Copie #text(get-field(data, "copie_id", default-data.copie_id))]
#v(14pt)

#block(width: 100%, fill: rgb("#f0f6f3"), inset: 13pt, radius: 3pt)[
  #grid(
    columns: (1fr, 1fr), gutter: 12pt,
    [*Nom :* #text(get-field(data, "nom", default-data.nom))],
    [*Prénom :* #text(get-field(data, "prenom", default-data.prenom))],
    [*Note globale :* #text(get-field(data, "note_globale", default-data.note_globale))],
    [*Moyenne de classe :* #text(get-field(data, "moyenne_promotion", default-data.moyenne_promotion))],
    [*Note sur 20 :* #text(get-field(data, "note_sur_20", default-data.note_sur_20))],
    [*Moyenne sur 20 :* #text(get-field(data, "moyenne_sur_20", default-data.moyenne_sur_20))],
  )
]
#v(15pt)

#for question in get-field(data, "questions", ()) {
  [
    #for title in get-field(question, "section_titles", ()) {
      text(size: 15pt, weight: "bold", fill: ink)[#rich-text-noreponse(title)]
      v(8pt)
    }
    #block(breakable: false)[
      #text(size: 13pt, weight: "bold", fill: ink)[#text(get-field(question, "titre", "Question"))]
      #h(1fr)
      #text(size: 15pt, weight: "bold", fill: ink)[#text(get-field(question, "note", "Non notée"))]
    ]
    #v(6pt)
    #if get-field(question, "enonce", "") != "" {
      panel("Énoncé", rich-text-noreponse(get-field(question, "enonce", "")), rgb("#eef3f6"), rgb("#356b8b"))
    }
    #v(7pt)
    #if get-field(question, "corrige", "") != "" {
      panel("Corrigé", rich-text-noreponse(get-field(question, "corrige", "")), rgb("#edf8f1"), rgb("#207a50"))
    }
    #v(7pt)
    #if get-field(question, "appreciation", "") != "" {
      panel("Appréciation du correcteur", rich-text-comment(get-field(question, "appreciation", "")), rgb("#fdf0ef"), rgb("#ba3835"))
    }
    #if get-field(question, "crop_image_path", "") != "" {
      v(8pt)
      align(center)[
        #image(get-field(question, "crop_image_path", ""), width: 85%)
      ]
    }
    #v(13pt)
    #line(length: 100%, stroke: 0.5pt + rgb("#d8e3df"))
    #v(13pt)
  ]
}