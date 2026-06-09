const HTML = "<!doctype html>\n<html lang=\"en\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n    <title>CFO IT Dashboard</title>\n    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />\n    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />\n    <link\n      href=\"https://fonts.googleapis.com/css2?family=Alegreya+Sans:wght@400;500;700;800&family=Chivo:wght@600;700;800;900&display=swap\"\n      rel=\"stylesheet\"\n    />\n    <style>\n      :root {\n        --paper: oklch(0.965 0.014 82);\n        --paper-deep: oklch(0.915 0.026 82);\n        --ink: oklch(0.22 0.038 245);\n        --muted: oklch(0.43 0.03 245);\n        --line: oklch(0.82 0.025 82);\n        --navy: oklch(0.34 0.087 246);\n        --navy-soft: oklch(0.62 0.08 240);\n        --brass: oklch(0.79 0.145 79);\n        --rust: oklch(0.58 0.14 42);\n        --moss: oklch(0.48 0.08 145);\n        --white: oklch(0.985 0.006 82);\n        --space-1: 0.25rem;\n        --space-2: 0.5rem;\n        --space-3: 0.75rem;\n        --space-4: 1rem;\n        --space-6: 1.5rem;\n        --space-8: 2rem;\n        --space-12: 3rem;\n        --space-16: 4rem;\n        color-scheme: light;\n      }\n\n      * {\n        box-sizing: border-box;\n      }\n\n      body {\n        margin: 0;\n        min-height: 100vh;\n        color: var(--ink);\n        background:\n          radial-gradient(circle at 8% 10%, oklch(0.9 0.07 79 / 0.7), transparent 28rem),\n          radial-gradient(circle at 88% 5%, oklch(0.78 0.08 235 / 0.28), transparent 32rem),\n          linear-gradient(135deg, var(--paper), oklch(0.945 0.018 94));\n        font-family: \"Alegreya Sans\", ui-sans-serif, sans-serif;\n      }\n\n      .page {\n        width: min(1180px, calc(100% - 2rem));\n        margin: 0 auto;\n        padding: clamp(1.25rem, 4vw, 4.5rem) 0;\n      }\n\n      .shell {\n        position: relative;\n        overflow: hidden;\n        border: 1px solid color-mix(in oklch, var(--line), var(--navy) 10%);\n        border-radius: 30px;\n        background: color-mix(in oklch, var(--white), var(--paper) 18%);\n        box-shadow: 0 30px 90px oklch(0.22 0.038 245 / 0.16);\n      }\n\n      .shell::before {\n        content: \"\";\n        position: absolute;\n        inset: 0;\n        pointer-events: none;\n        background-image:\n          linear-gradient(90deg, oklch(0.22 0.038 245 / 0.035) 1px, transparent 1px),\n          linear-gradient(0deg, oklch(0.22 0.038 245 / 0.035) 1px, transparent 1px);\n        background-size: 36px 36px;\n        mask-image: linear-gradient(180deg, #000 0%, transparent 62%);\n      }\n\n      header {\n        position: relative;\n        display: grid;\n        grid-template-columns: 1.4fr 0.8fr;\n        gap: var(--space-12);\n        padding: clamp(1.5rem, 5vw, 4rem);\n        border-bottom: 1px solid var(--line);\n      }\n\n      .eyebrow,\n      .label,\n      .micro {\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        text-transform: uppercase;\n        letter-spacing: 0.14em;\n        font-weight: 800;\n      }\n\n      .eyebrow {\n        display: inline-flex;\n        align-items: center;\n        gap: var(--space-2);\n        width: fit-content;\n        padding: 0.42rem 0.72rem;\n        border: 1px solid color-mix(in oklch, var(--navy), var(--paper) 55%);\n        border-radius: 999px;\n        color: var(--navy);\n        background: oklch(0.97 0.018 85 / 0.78);\n        font-size: 0.72rem;\n      }\n\n      h1 {\n        max-width: 10ch;\n        margin: var(--space-6) 0 var(--space-4);\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        font-size: clamp(3.1rem, 8vw, 7.8rem);\n        line-height: 0.84;\n        letter-spacing: -0.085em;\n      }\n\n      .deck {\n        max-width: 58ch;\n        margin: 0;\n        color: var(--muted);\n        font-size: clamp(1.1rem, 1.7vw, 1.45rem);\n        line-height: 1.35;\n      }\n\n      .period-card {\n        align-self: end;\n        display: grid;\n        gap: var(--space-4);\n        padding: var(--space-6);\n        border-radius: 24px;\n        color: var(--white);\n        background:\n          linear-gradient(160deg, oklch(0.26 0.06 248), var(--navy) 70%),\n          var(--navy);\n      }\n\n      .period-card strong {\n        display: block;\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        font-size: clamp(2rem, 5vw, 4.8rem);\n        line-height: 0.9;\n        letter-spacing: -0.06em;\n      }\n\n      .period-card span {\n        color: oklch(0.88 0.035 85);\n        font-size: 1.05rem;\n      }\n\n      main {\n        position: relative;\n        display: grid;\n        gap: var(--space-8);\n        padding: clamp(1rem, 4vw, 3rem);\n      }\n\n      .metric-grid {\n        display: grid;\n        grid-template-columns: 1fr 1fr 1.05fr;\n        gap: var(--space-4);\n      }\n\n      .metric {\n        min-height: 205px;\n        display: grid;\n        align-content: space-between;\n        gap: var(--space-4);\n        padding: clamp(1.25rem, 2.4vw, 2rem);\n        border: 1px solid var(--line);\n        border-radius: 24px;\n        background: oklch(0.985 0.009 82 / 0.86);\n      }\n\n      .metric.major {\n        color: var(--white);\n        background: linear-gradient(145deg, var(--navy), oklch(0.28 0.072 248));\n        border-color: transparent;\n      }\n\n      .label {\n        color: color-mix(in oklch, currentColor, var(--muted) 46%);\n        font-size: 0.72rem;\n      }\n\n      .metric .value {\n        display: block;\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        font-size: clamp(3rem, 7vw, 6rem);\n        line-height: 0.85;\n        letter-spacing: -0.075em;\n      }\n\n      .metric p {\n        margin: 0;\n        color: color-mix(in oklch, currentColor, var(--muted) 32%);\n        font-size: 1.04rem;\n        line-height: 1.35;\n      }\n\n      .delta {\n        display: inline-flex;\n        width: fit-content;\n        align-items: center;\n        gap: var(--space-2);\n        padding: 0.34rem 0.58rem;\n        border-radius: 999px;\n        background: oklch(0.89 0.05 80);\n        color: oklch(0.34 0.08 47);\n        font-weight: 800;\n      }\n\n      .major .delta {\n        background: oklch(0.84 0.12 79);\n        color: oklch(0.2 0.04 248);\n      }\n\n      .panel-grid {\n        display: grid;\n        grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.65fr);\n        gap: var(--space-4);\n      }\n\n      .panel {\n        padding: clamp(1.25rem, 2.5vw, 2rem);\n        border: 1px solid var(--line);\n        border-radius: 26px;\n        background: oklch(0.985 0.007 82 / 0.9);\n      }\n\n      .panel-head {\n        display: flex;\n        align-items: start;\n        justify-content: space-between;\n        gap: var(--space-6);\n        margin-bottom: var(--space-6);\n      }\n\n      h2 {\n        margin: 0;\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        font-size: clamp(1.6rem, 3vw, 2.7rem);\n        line-height: 0.95;\n        letter-spacing: -0.055em;\n      }\n\n      .note {\n        margin: var(--space-2) 0 0;\n        max-width: 48ch;\n        color: var(--muted);\n        font-size: 1rem;\n      }\n\n      .chart {\n        width: 100%;\n        min-height: 320px;\n      }\n\n      .legend {\n        display: flex;\n        flex-wrap: wrap;\n        gap: var(--space-3);\n        margin-top: var(--space-4);\n      }\n\n      .legend span {\n        display: inline-flex;\n        align-items: center;\n        gap: var(--space-2);\n        color: var(--muted);\n        font-weight: 700;\n      }\n\n      .dot {\n        width: 0.68rem;\n        height: 0.68rem;\n        border-radius: 999px;\n        background: var(--navy);\n      }\n\n      .dot.closed {\n        background: var(--brass);\n      }\n\n      .dot.open {\n        background: var(--rust);\n      }\n\n      .survey-score {\n        display: grid;\n        grid-template-columns: auto 1fr;\n        gap: var(--space-4);\n        align-items: center;\n        margin-bottom: var(--space-6);\n      }\n\n      .roundel {\n        display: grid;\n        place-items: center;\n        width: 112px;\n        aspect-ratio: 1;\n        border-radius: 999px;\n        color: var(--ink);\n        background: conic-gradient(var(--brass) 0turn 0.96turn, var(--paper-deep) 0.96turn);\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        font-size: 2.2rem;\n        font-weight: 900;\n        letter-spacing: -0.07em;\n      }\n\n      .bar-list {\n        display: grid;\n        gap: var(--space-3);\n      }\n\n      .bar-row {\n        display: grid;\n        grid-template-columns: 8ch 1fr 4ch;\n        gap: var(--space-3);\n        align-items: center;\n        color: var(--muted);\n        font-weight: 700;\n      }\n\n      .bar-track {\n        height: 0.75rem;\n        overflow: hidden;\n        border-radius: 999px;\n        background: var(--paper-deep);\n      }\n\n      .bar-fill {\n        height: 100%;\n        border-radius: 999px;\n        background: linear-gradient(90deg, var(--navy), var(--brass));\n      }\n\n      .voice-grid {\n        display: grid;\n        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));\n        gap: var(--space-4);\n      }\n\n      blockquote {\n        margin: 0;\n        min-height: 180px;\n        display: grid;\n        align-content: space-between;\n        gap: var(--space-4);\n        padding: var(--space-5, 1.25rem);\n        border: 1px solid color-mix(in oklch, var(--line), var(--brass) 28%);\n        border-radius: 22px;\n        background: linear-gradient(180deg, oklch(0.98 0.017 84), oklch(0.95 0.024 84));\n      }\n\n      blockquote p {\n        margin: 0;\n        font-size: 1.08rem;\n        line-height: 1.34;\n      }\n\n      cite {\n        color: var(--muted);\n        font-style: normal;\n        font-weight: 800;\n      }\n\n      .projects {\n        display: grid;\n        grid-template-columns: 0.9fr 1.1fr;\n        gap: var(--space-6);\n        align-items: start;\n      }\n\n      .team-stack,\n      .project-list {\n        display: grid;\n        gap: var(--space-3);\n      }\n\n      .team-row,\n      .project-row {\n        display: grid;\n        gap: var(--space-2);\n        padding: var(--space-3) 0;\n        border-bottom: 1px solid var(--line);\n      }\n\n      .team-row:last-child,\n      .project-row:last-child {\n        border-bottom: 0;\n      }\n\n      .team-row strong,\n      .project-row strong {\n        font-family: \"Chivo\", ui-sans-serif, sans-serif;\n        letter-spacing: -0.025em;\n      }\n\n      .project-meta {\n        display: flex;\n        flex-wrap: wrap;\n        gap: var(--space-2);\n        color: var(--muted);\n        font-weight: 700;\n      }\n\n      .tag {\n        display: inline-flex;\n        width: fit-content;\n        padding: 0.12rem 0.45rem;\n        border-radius: 999px;\n        background: var(--brass);\n        color: var(--ink);\n        font-size: 0.75rem;\n        font-weight: 900;\n      }\n\n      footer {\n        padding: var(--space-8) clamp(1.5rem, 4vw, 3rem) var(--space-10, 2.5rem);\n        color: var(--muted);\n        border-top: 1px solid var(--line);\n      }\n\n      .fade-in {\n        animation: rise 680ms cubic-bezier(0.22, 1, 0.36, 1) both;\n      }\n\n      @keyframes rise {\n        from {\n          opacity: 0;\n          transform: translateY(18px);\n        }\n        to {\n          opacity: 1;\n          transform: translateY(0);\n        }\n      }\n\n      @media (prefers-reduced-motion: reduce) {\n        .fade-in {\n          animation: none;\n        }\n      }\n\n      @media (max-width: 860px) {\n        header,\n        .metric-grid,\n        .panel-grid,\n        .projects {\n          grid-template-columns: 1fr;\n        }\n\n        h1 {\n          max-width: 8ch;\n        }\n      }\n    </style>\n  </head>\n  <body>\n    <div class=\"page\">\n      <div class=\"shell\">\n        <header class=\"fade-in\">\n          <section>\n            <div class=\"eyebrow\">CFO weekly signal</div>\n            <h1>IT, with receipts.</h1>\n            <p class=\"deck\">\n              A sharper dashboard cut from the CFO email: intake, closure, backlog, survey\n              sentiment, and project movement. No fog machine included.\n            </p>\n          </section>\n          <aside class=\"period-card\" aria-label=\"Reporting period\">\n            <div class=\"label\">Current period</div>\n            <strong id=\"period\">Loading</strong>\n            <span id=\"generated\">Refreshing the briefing table.</span>\n          </aside>\n        </header>\n\n        <main>\n          <section class=\"metric-grid fade-in\" style=\"animation-delay: 80ms\">\n            <article class=\"metric major\">\n              <div class=\"label\">Created tickets</div>\n              <span class=\"value\" id=\"createdValue\">--</span>\n              <p><span class=\"delta\" id=\"createdDelta\">--</span> versus prior period. Demand did not send a calendar invite.</p>\n            </article>\n            <article class=\"metric\">\n              <div class=\"label\">Closed tickets</div>\n              <span class=\"value\" id=\"closedValue\">--</span>\n              <p><span class=\"delta\" id=\"closedDelta\">--</span> versus prior period. Progress, with a paper trail.</p>\n            </article>\n            <article class=\"metric\">\n              <div class=\"label\">Open backlog</div>\n              <span class=\"value\" id=\"openValue\">--</span>\n              <p><span class=\"delta\" id=\"openDelta\">--</span> since last period. Backlog remains the houseplant of operations.</p>\n            </article>\n          </section>\n\n          <section class=\"panel-grid\">\n            <article class=\"panel fade-in\" style=\"animation-delay: 140ms\">\n              <div class=\"panel-head\">\n                <div>\n                  <div class=\"label\">Eight-week volume</div>\n                  <h2>What moved, what closed, what stayed.</h2>\n                  <p class=\"note\">Created and closed volume are weekly totals. Open backlog is the count at each week boundary.</p>\n                </div>\n              </div>\n              <div id=\"volumeChart\" class=\"chart\" role=\"img\" aria-label=\"Eight-week ticket volume chart\"></div>\n              <div class=\"legend\">\n                <span><i class=\"dot\"></i>Created</span>\n                <span><i class=\"dot closed\"></i>Closed</span>\n                <span><i class=\"dot open\"></i>Open backlog</span>\n              </div>\n            </article>\n\n            <article class=\"panel fade-in\" style=\"animation-delay: 200ms\">\n              <div class=\"label\">Survey temperature</div>\n              <h2>People were not subtle.</h2>\n              <div class=\"survey-score\">\n                <div class=\"roundel\" id=\"surveyRoundel\">--</div>\n                <p class=\"note\" id=\"surveySummary\">Loading survey results.</p>\n              </div>\n              <div class=\"bar-list\" id=\"surveyBars\"></div>\n            </article>\n          </section>\n\n          <section class=\"panel fade-in\" style=\"animation-delay: 260ms\">\n            <div class=\"panel-head\">\n              <div>\n                <div class=\"label\">Customer voice</div>\n                <h2>Useful praise, redacted enough for daylight.</h2>\n                <p class=\"note\">Names and full comments are intentionally omitted here. The CFO email can carry the raw detail; a website should not cosplay as a personnel file.</p>\n              </div>\n            </div>\n            <div class=\"voice-grid\" id=\"voiceGrid\"></div>\n          </section>\n\n          <section class=\"panel projects fade-in\" style=\"animation-delay: 320ms\">\n            <div>\n              <div class=\"label\">Project movement</div>\n              <h2 id=\"projectHeadline\">Project board loading.</h2>\n              <p class=\"note\" id=\"projectNote\">Gathering active project movement.</p>\n              <div class=\"team-stack\" id=\"teamStack\"></div>\n            </div>\n            <div>\n              <div class=\"label\">Sample active work</div>\n              <div class=\"project-list\" id=\"projectList\"></div>\n            </div>\n          </section>\n        </main>\n\n        <footer>\n          Built from the same CFO email snapshot logic, refreshed from TeamDynamix and YouTrack before publication.\n        </footer>\n      </div>\n    </div>\n\n    <script>\n      const number = new Intl.NumberFormat(\"en-US\");\n      const pct = new Intl.NumberFormat(\"en-US\", { maximumFractionDigits: 1, signDisplay: \"always\" });\n\n      const text = (id, value) => {\n        document.getElementById(id).textContent = value;\n      };\n\n      const deltaPercent = (value) => {\n        if (value === null || value === undefined || Number.isNaN(Number(value))) return \"no prior data\";\n        return `${pct.format(value)}%`;\n      };\n\n      const deltaCount = (value) => {\n        if (value === null || value === undefined || Number.isNaN(Number(value))) return \"no prior data\";\n        const sign = Number(value) > 0 ? \"+\" : \"\";\n        return `${sign}${number.format(value)}`;\n      };\n\n      const sum = (values) => values.reduce((total, item) => total + Number(item.count || 0), 0);\n\n      function renderVolumeChart(data) {\n        const created = data.weekly.created || [];\n        const closed = data.weekly.closed || [];\n        const open = data.weekly.open || [];\n        const weeks = created.map((item) => item.week);\n        const maxVolume = Math.max(1, ...created.map((x) => x.count), ...closed.map((x) => x.count));\n        const maxOpen = Math.max(1, ...open.map((x) => x.count));\n        const width = 980;\n        const height = 330;\n        const pad = { top: 24, right: 32, bottom: 58, left: 44 };\n        const plotW = width - pad.left - pad.right;\n        const plotH = height - pad.top - pad.bottom;\n        const slot = plotW / Math.max(1, weeks.length);\n        const barW = Math.min(28, slot * 0.24);\n        const openPoints = open.map((item, index) => {\n          const x = pad.left + slot * index + slot / 2;\n          const y = pad.top + plotH - (Number(item.count || 0) / maxOpen) * plotH;\n          return `${x},${y}`;\n        });\n\n        const bars = weeks\n          .map((week, index) => {\n            const baseX = pad.left + slot * index + slot / 2;\n            const createdH = (Number(created[index]?.count || 0) / maxVolume) * plotH;\n            const closedH = (Number(closed[index]?.count || 0) / maxVolume) * plotH;\n            return `\n              <rect x=\"${baseX - barW - 2}\" y=\"${pad.top + plotH - createdH}\" width=\"${barW}\" height=\"${createdH}\" rx=\"5\" fill=\"var(--navy)\" />\n              <rect x=\"${baseX + 2}\" y=\"${pad.top + plotH - closedH}\" width=\"${barW}\" height=\"${closedH}\" rx=\"5\" fill=\"var(--brass)\" />\n              <text x=\"${baseX}\" y=\"${height - 24}\" text-anchor=\"middle\" font-size=\"16\" font-weight=\"700\" fill=\"var(--muted)\">${week}</text>\n            `;\n          })\n          .join(\"\");\n\n        document.getElementById(\"volumeChart\").innerHTML = `\n          <svg viewBox=\"0 0 ${width} ${height}\" width=\"100%\" height=\"100%\" preserveAspectRatio=\"none\">\n            <line x1=\"${pad.left}\" y1=\"${pad.top + plotH}\" x2=\"${width - pad.right}\" y2=\"${pad.top + plotH}\" stroke=\"var(--line)\" />\n            ${bars}\n            <polyline points=\"${openPoints.join(\" \")}\" fill=\"none\" stroke=\"var(--rust)\" stroke-width=\"5\" stroke-linecap=\"round\" stroke-linejoin=\"round\" />\n            ${openPoints.map((point) => {\n              const [x, y] = point.split(\",\");\n              return `<circle cx=\"${x}\" cy=\"${y}\" r=\"6\" fill=\"var(--white)\" stroke=\"var(--rust)\" stroke-width=\"4\" />`;\n            }).join(\"\")}\n          </svg>\n        `;\n      }\n\n      function renderSurvey(data) {\n        const weekTotal = Number(data.survey.weekTotal || 0);\n        const yearTotal = Number(data.survey.yearTotal || 0);\n        const great = Number(data.survey.weekCounts?.[\"Great!\"] || 0);\n        text(\"surveyRoundel\", weekTotal ? `${great}/${weekTotal}` : \"--\");\n        text(\n          \"surveySummary\",\n          weekTotal\n            ? `${great} of ${weekTotal} current-period survey responses landed as Great. The trailing year has ${yearTotal} responses for comparison.`\n            : `No current-period survey responses. The trailing year has ${yearTotal} responses.`\n        );\n\n        const counts = data.survey.yearCounts || {};\n        const max = Math.max(1, ...Object.values(counts).map(Number));\n        document.getElementById(\"surveyBars\").innerHTML = Object.entries(counts)\n          .map(([label, count]) => `\n            <div class=\"bar-row\">\n              <span>${label}</span>\n              <div class=\"bar-track\"><div class=\"bar-fill\" style=\"width:${(Number(count) / max) * 100}%\"></div></div>\n              <strong>${count}</strong>\n            </div>\n          `)\n          .join(\"\");\n      }\n\n      function renderVoice(data) {\n        const comments = data.survey.comments || [];\n        document.getElementById(\"voiceGrid\").innerHTML = comments\n          .slice(0, 6)\n          .map((comment) => `\n            <blockquote>\n              <p>\u201c${comment.excerpt}\u201d</p>\n              <cite>${comment.sentiment} \u00b7 ${comment.date}</cite>\n            </blockquote>\n          `)\n          .join(\"\");\n      }\n\n      function renderProjects(data) {\n        const movement = data.projects.movement || {};\n        const inProgress = Number(movement.in_progress_count || 0);\n        const newCount = Number(movement.new_this_week || 0);\n        const doneCount = Number(movement.completed_this_week || 0);\n        text(\n          \"projectHeadline\",\n          `${inProgress} active projects. ${newCount} new. ${doneCount} completed.`\n        );\n        text(\n          \"projectNote\",\n          newCount || doneCount\n            ? \"The board moved. Someone may have touched the tracker with intent.\"\n            : \"No new or completed project movement this week. The board is alive, just not theatrical.\"\n        );\n\n        const teams = Object.entries(data.projects.teamCounts || {}).sort((a, b) => b[1] - a[1]);\n        const maxTeam = Math.max(1, ...teams.map(([, count]) => count));\n        document.getElementById(\"teamStack\").innerHTML = teams\n          .map(([team, count]) => `\n            <div class=\"team-row\">\n              <strong>${team}</strong>\n              <div class=\"bar-track\"><div class=\"bar-fill\" style=\"width:${(Number(count) / maxTeam) * 100}%\"></div></div>\n              <span class=\"micro\">${count} active</span>\n            </div>\n          `)\n          .join(\"\");\n\n        document.getElementById(\"projectList\").innerHTML = (data.projects.samples || [])\n          .map((project) => `\n            <div class=\"project-row\">\n              <strong>${project.summary}</strong>\n              <div class=\"project-meta\">\n                <span>${project.id}</span>\n                <span>${project.team}</span>\n                ${project.isNew ? '<span class=\"tag\">New</span>' : \"\"}\n              </div>\n            </div>\n          `)\n          .join(\"\");\n      }\n\n      async function boot() {\n        const response = await fetch(\"./data.json\");\n        const data = await response.json();\n        text(\"period\", data.period);\n        text(\"generated\", `Generated ${new Date(data.generatedAt).toLocaleString([], { dateStyle: \"medium\", timeStyle: \"short\" })}`);\n        text(\"createdValue\", number.format(data.metrics.created));\n        text(\"closedValue\", number.format(data.metrics.closed));\n        text(\"openValue\", number.format(data.metrics.open));\n        text(\"createdDelta\", deltaPercent(data.metrics.createdDelta));\n        text(\"closedDelta\", deltaPercent(data.metrics.closedDelta));\n        text(\"openDelta\", deltaCount(data.metrics.openDelta));\n        renderVolumeChart(data);\n        renderSurvey(data);\n        renderVoice(data);\n        renderProjects(data);\n      }\n\n      boot().catch((error) => {\n        document.body.innerHTML = `<main style=\"padding:2rem;font-family:sans-serif\"><h1>Dashboard failed to load.</h1><pre>${error}</pre></main>`;\n      });\n    </script>\n  </body>\n</html>\n";
const DATA = {
  "period": "Jun 2 – Jun 9",
  "generatedAt": "2026-06-09T12:46:16.918742+00:00",
  "metrics": {
    "created": 464,
    "createdPrior": 348,
    "createdYearAgo": 207,
    "createdDelta": 33.33333333333333,
    "closed": 522,
    "closedPrior": 307,
    "closedYearAgo": 232,
    "closedDelta": 70.03257328990227,
    "open": 583,
    "openPrior": 655,
    "openDelta": -72
  },
  "weekly": {
    "created": [
      {
        "week": "Apr 14",
        "count": 286
      },
      {
        "week": "Apr 21",
        "count": 278
      },
      {
        "week": "Apr 28",
        "count": 305
      },
      {
        "week": "May 5",
        "count": 255
      },
      {
        "week": "May 12",
        "count": 298
      },
      {
        "week": "May 19",
        "count": 236
      },
      {
        "week": "May 26",
        "count": 348
      },
      {
        "week": "Jun 2",
        "count": 464
      }
    ],
    "closed": [
      {
        "week": "Apr 14",
        "count": 275
      },
      {
        "week": "Apr 21",
        "count": 252
      },
      {
        "week": "Apr 28",
        "count": 270
      },
      {
        "week": "May 5",
        "count": 247
      },
      {
        "week": "May 12",
        "count": 318
      },
      {
        "week": "May 19",
        "count": 235
      },
      {
        "week": "May 26",
        "count": 307
      },
      {
        "week": "Jun 2",
        "count": 522
      }
    ],
    "open": [
      {
        "week": "Apr 14",
        "count": 724
      },
      {
        "week": "Apr 21",
        "count": 731
      },
      {
        "week": "Apr 28",
        "count": 738
      },
      {
        "week": "May 5",
        "count": 719
      },
      {
        "week": "May 12",
        "count": 671
      },
      {
        "week": "May 19",
        "count": 640
      },
      {
        "week": "May 26",
        "count": 655
      },
      {
        "week": "Jun 2",
        "count": 583
      }
    ]
  },
  "survey": {
    "weekCounts": {
      "Great!": 5,
      "Not good at all": 1
    },
    "weekTotal": 6,
    "yearCounts": {
      "Great!": 83,
      "Could have been better": 5,
      "Not good at all": 1,
      "Okay": 2
    },
    "yearTotal": 91,
    "comments": [
      {
        "date": "2026-06-08",
        "sentiment": "Great!",
        "excerpt": "Hoping to get temporary monitors to set up for our temp's use with her MacBook air. For now, she is set up to use my monitors while I am out of office and working remotely.

It was easy to make a request and discuss opt…"
      },
      {
        "date": "2026-06-06",
        "sentiment": "Great!",
        "excerpt": "I received a timely response that was clear and resolved my issue."
      },
      {
        "date": "2026-06-04",
        "sentiment": "Not good at all",
        "excerpt": "I do not understand the response .... is this a new response method?  I truly do not understand if this is legitimate or phishing.  As a result, I deleted both emails.

Quickly ...."
      },
      {
        "date": "2026-06-04",
        "sentiment": "Great!",
        "excerpt": "Everything fixed and operational again.

It was really difficult to get anyone to answer the phone initially. That was frustrating.
Once I got to Noah Orme the service was exceptional. He was so helpful and responsive.…"
      },
      {
        "date": "2026-06-04",
        "sentiment": "Great!",
        "excerpt": "Again, your team did a wonderful job in making sure that the information provided was clear and concise!

The clarity of communication was key and your team did a fantastic job ensuring everyone was on the same page!"
      },
      {
        "date": "2026-06-02",
        "sentiment": "Great!",
        "excerpt": "Thank for Sophia's quick and effective response.

Sophia was very responsive and knowledgeable."
      }
    ]
  },
  "projects": {
    "movement": {
      "in_progress_count": 19,
      "new_this_week": 0,
      "completed_this_week": 0
    },
    "teamCounts": {
      "Tech Ops": 8,
      "Tech Pros": 8,
      "Tech Services": 2,
      "Security": 1
    },
    "samples": [
      {
        "id": "TOPS-1922",
        "summary": "Strongest Layer implementation",
        "team": "Security",
        "isNew": false
      },
      {
        "id": "TOPS-1144",
        "summary": "Bolthouse Academic Center Construction",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-1426",
        "summary": "Construction - New Apartments",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-1623",
        "summary": "Construction - Track and Soccer Press Box",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-1147",
        "summary": "IP Address Management Solution",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-1940",
        "summary": "Replace Failing UPSs",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-2038",
        "summary": "Summer 2026 - Software Updates",
        "team": "Tech Ops",
        "isNew": false
      },
      {
        "id": "TOPS-1140",
        "summary": "Windows Server 2016 Upgrades",
        "team": "Tech Ops",
        "isNew": false
      }
    ]
  }
};

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/data.json") {
      return new Response(JSON.stringify(DATA), { headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=300" } });
    }
    return new Response(HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" } });
  }
};
