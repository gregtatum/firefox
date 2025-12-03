/* Any copyright is dedicated to the Public Domain.
   http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

const referenceText = `Guided Walk in the Valley
The forest path is quiet in the early morning and the river reflects the sky.
A red fox waits near the water and watches the trees.
Travelers carry a notebook to record the names of birds they hear.
After sunset, lanterns guide them back to the cabin.
`;

const evalMetadata = {
  owner: "Translations Team",
  name: "Full-Page Translation E2E Eval",
  description:
    "End-to-end translation quality evaluation for full-page translations.",
  test: "mochitest",
  options: {
    default: {
      manifest: "eval.toml",
      manifest_flavor: "browser-chrome",
    },
  },
};

add_task(async function test_full_page_e2e_eval() {
  const markup = html`
    <article>
      <h1>Paseo guiado en el valle</h1>
      <p>
        El sendero del bosque está tranquilo en la madrugada y el río refleja el
        cielo.
      </p>
      <p>Un zorro rojo espera cerca del agua y observa los árboles.</p>
      <p>
        Los viajeros llevan un cuaderno para anotar los nombres de las aves que
        escuchan.
      </p>
      <p>
        Después del atardecer, las linternas los guían de regreso a la cabaña.
      </p>
    </article>
  `;

  const { tab, cleanup } = await setupEvaluation({
    markup,
    endToEndTest: true,
    architecture: "tiny",
    languagePairs: LANGUAGE_PAIRS,
    appLocales: ["en"],
    systemLocales: ["en"],
    webLanguages: ["en"],
  });

  const getPageText = () => {
    return SpecialPowers.spawn(tab.linkedBrowser, [], async () => {
      return [...content.document.querySelectorAll("article > *")]
        .map(el => el.innerText)
        .join("\n");
    });
  };

  const sourceText = await getPageText();

  const articleTranslated = waitForMutations(tab.linkedBrowser, "article > *");

  await FullPageTranslationsTestUtils.assertTranslationsButton(
    { button: true, circleArrows: false, locale: false, icon: true },
    "The translations button is available."
  );

  await FullPageTranslationsTestUtils.openPanel({
    expectedFromLanguage: "es",
    expectedToLanguage: "en",
    onOpenPanel: FullPageTranslationsTestUtils.assertPanelViewIntro,
  });

  await FullPageTranslationsTestUtils.clickTranslateButton();

  await articleTranslated;

  const translatedText = await getPageText();
  Assert.notEqual(sourceText, translatedText, "The text was translated.");

  reportEvalResult({
    type: "translation",
    src: sourceText,
    trg: translatedText,
    ref: referenceText,
  });

  await cleanup();
});
