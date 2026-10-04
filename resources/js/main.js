Neutralino.init();
Neutralino.events.on("windowClose", () => {
    Neutralino.app.exit();
});

// hlavní strana aplikace, ještě idk jak ji rozdělím