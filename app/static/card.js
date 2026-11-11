const RadarCard = (() => {
  const STAMP = {
    sustentado: ["CORRETO", "#3dce8f"],
    sustentado_ressalva: ["CORRETO, COM RESSALVA", "#b7d36a"],
    nao_e_bem_assim: ["IMPRECISO", "#e2b34a"],
    exagerado: ["SUPERESTIMADO", "#f0a05a"],
    promessa: ["COMPROMISSO ANUNCIADO", "#f0a05a"],
    distorcido: ["DESCONTEXTUALIZADO", "#f3a0a8"],
    contradito: ["NÃO PROCEDE", "#ff6b6b"],
    sem_prova: ["SEM REGISTRO OFICIAL", "#c8c2ba"],
    erro: ["FALHA NA ANÁLISE", "#ff6b6b"],
    sem_prova_publica: ["SEM REGISTRO PÚBLICO", "#a9bdd4"],
    versoes_em_disputa: ["VERSÕES EM DISPUTA", "#a9bdd4"],
    indicio: ["INDÍCIO", "#a9bdd4"],
    dados_incompletos: ["DADOS INCOMPLETOS", "#a9bdd4"],
    audio_confuso: ["REGISTRO INCONSISTENTE", "#a9bdd4"],
    sem_evidencia: ["SEM EVIDÊNCIA SUFICIENTE", "#a9bdd4"],
    inconclusivo: ["EM VERIFICAÇÃO", "#a9bdd4"],
    pendente: ["NA FILA", "#a9bdd4"],
    processando: ["EM ANÁLISE", "#a9bdd4"],
  };

  const THEME = {
    saude: "Saúde",
    agua: "Água",
    transporte: "Transporte",
    limpeza: "Limpeza",
    atendimento: "Atendimento",
    outro: "Serviço público",
  };

  function wrap(ctx, text, maxW) {
    const words = String(text || "").split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width > maxW && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);
    return lines;
  }

  function face(size, display) {
    return `400 ${size}px ${display ? "'Archivo Black', sans-serif" : "Literata, serif"}`;
  }

  function drawStamp(ctx, text, ink, x, y, maxW) {
    ctx.save();
    let size = 42;
    ctx.font = face(size, true);
    while (size > 24 && ctx.measureText(text).width > maxW - 56) {
      size -= 2;
      ctx.font = face(size, true);
    }
    const w = Math.min(ctx.measureText(text).width + 48, maxW);
    const h = size + 34;
    ctx.translate(x, y + h / 2);
    ctx.rotate(-0.045);
    ctx.translate(0, -h / 2);
    ctx.strokeStyle = ink;
    ctx.lineWidth = 6;
    ctx.strokeRect(3, 3, w - 6, h - 6);
    ctx.lineWidth = 2;
    ctx.strokeRect(10, 10, w - 20, h - 20);
    ctx.fillStyle = ink;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, w / 2, h / 2 + 1);
    ctx.restore();
    return h;
  }

  async function faces() {
    if (document.fonts.check("16px 'Archivo Black'")) return;
    const black = new FontFace("Archivo Black", "url(/static/fonts/archivo-black.ttf)");
    const body = new FontFace("Literata", "url(/static/fonts/literata-500.ttf)", { weight: "500" });
    const bold = new FontFace("Literata", "url(/static/fonts/literata-700.ttf)", { weight: "700" });
    const loaded = await Promise.all([black.load(), body.load(), bold.load()]);
    loaded.forEach((item) => document.fonts.add(item));
  }

  async function draw(claim, format) {
    await faces();
    const story = format === "stories" || format === "reels";
    const W = 1080;
    const H = story ? 1920 : 1350;
    const pad = 88;
    const [stamp, ink] = STAMP[claim.status] || ["CHECAGEM", "#f6f3ee"];
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");

    ctx.fillStyle = "#0c0c0c";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#f15d5a";
    ctx.lineWidth = 12;
    ctx.strokeRect(18, 18, W - 36, H - 36);

    // soft glow accents
    const g1 = ctx.createRadialGradient(0, 0, 40, 180, 160, 520);
    g1.addColorStop(0, "rgba(241,93,90,0.18)");
    g1.addColorStop(1, "rgba(241,93,90,0)");
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W * 0.7, H * 0.45);
    const g2 = ctx.createRadialGradient(W, 0, 20, W - 120, 180, 480);
    g2.addColorStop(0, "rgba(74,184,72,0.14)");
    g2.addColorStop(1, "rgba(74,184,72,0)");
    ctx.fillStyle = g2;
    ctx.fillRect(W * 0.35, 0, W * 0.65, H * 0.4);

    const logo = new Image();
    logo.src = "/static/logo-ie-mark.png";
    await logo.decode();
    const logoH = story ? 92 : 78;
    const logoW = logoH * (logo.width / logo.height);
    let top = story ? 120 : 72;
    ctx.drawImage(logo, pad, top, logoW, logoH);
    top += logoH + 28;

    ctx.fillStyle = "#f15d5a";
    ctx.font = face(28, true);
    ctx.textBaseline = "top";
    ctx.fillText("RADAR MUNICIPAL", pad, top);

    ctx.fillStyle = "#f6f3ee";
    ctx.font = face(30, false);
    const theme = THEME[claim.theme] || "Ribeirão Preto";
    ctx.fillText(`Ribeirão Preto · ${theme}`, pad, top + 46);

    const quote = `"${claim.normalized || claim.quote || claim.title || ""}"`;
    ctx.font = face(story ? 58 : 54, true);
    const quoteLines = wrap(ctx, quote, W - pad * 2).slice(0, story ? 8 : 5);
    let y = top + (story ? 130 : 110);
    quoteLines.forEach((line) => {
      ctx.fillText(line, pad, y);
      y += story ? 70 : 66;
    });

    y += story ? 42 : 30;
    const stampH = drawStamp(ctx, stamp, ink, pad, y, W - pad * 2);
    ctx.textAlign = "left";
    ctx.textBaseline = "top";

    y += stampH + (story ? 42 : 30);
    ctx.fillStyle = "#f6f3ee";
    ctx.font = face(34, false);
    wrap(ctx, claim.summary || "", W - pad * 2)
      .slice(0, story ? 7 : 4)
      .forEach((line) => {
        ctx.fillText(line, pad, y);
        y += 46;
      });

    const source = (claim.sources || []).find((item) => item && item.url);
    const foot = H - (story ? 240 : 160);
    ctx.fillStyle = "#f15d5a";
    ctx.fillRect(pad, foot - 28, W - pad * 2, 3);
    ctx.fillStyle = "#f6f3ee";
    ctx.font = face(28, true);
    ctx.fillText("FONTE OFICIAL", pad, foot);
    ctx.font = face(28, false);
    const sourceText = source
      ? `${source.title || source.url}`
      : claim.portal_name
        ? `Portal · ${claim.portal_name}`
        : "Base municipal / portal oficial";
    wrap(ctx, sourceText, W - pad * 2).slice(0, 2).forEach((line, index) => {
      ctx.fillText(line, pad, foot + 42 + index * 36);
    });

    ctx.fillStyle = "#b7b0a6";
    ctx.font = face(22, true);
    ctx.fillText(
      story ? "REELS / STORIES · INTELIGÊNCIA ELEITORAL" : "IMAGEM PARA WHATSAPP · INTELIGÊNCIA ELEITORAL",
      pad,
      H - (story ? 90 : 70)
    );

    return await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  }

  function filename(claim, format) {
    const kind = format === "stories" || format === "reels" ? "reels" : "whatsapp";
    return `radar-ribeirao-${claim.id || "checagem"}-${kind}.png`;
  }

  function download(blob, name) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1500);
  }

  async function png(claim, format) {
    const blob = await draw(claim, format);
    download(blob, filename(claim, format));
  }

  async function share(claim, format) {
    const blob = await draw(claim, format || "feed");
    const image = new File([blob], filename(claim, format || "feed"), { type: "image/png" });
    if (navigator.canShare && navigator.canShare({ files: [image] })) {
      await navigator.share({
        files: [image],
        title: "Radar Municipal · Ribeirão Preto",
      });
      return "shared";
    }
    download(blob, image.name);
    return "saved";
  }

  return { png, share, draw };
})();
