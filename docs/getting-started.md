# Getting started

This release is a **beta** (version 0.1.0b1). Voice is on the roadmap and is not in this version.

Jig needs two things: Jig itself, and a model that can call tools through an OpenAI-compatible endpoint. The model can run on your computer (llama.cpp, including forks, Ollama, LM Studio or vLLM) or you can bring a cloud model. Jig checks the model for real before the agent starts, and it never falls back to another one.

The installer clicks, the SmartScreen warning, the measured model and the long troubleshooting list are in the README under [Get started](../README.md#get-started). This page is the short path.

## 1. Install Jig

**Do not run `pip install jig`.** That name on PyPI is an unrelated project. Use the installer, or install from this repository.

**Windows installer.** Download `JigSetup-<version>.exe` from the [v0.1.0b1 release](https://github.com/rlesueur/jig/releases/tag/v0.1.0b1) and open it. It needs no administrator rights and no Python. The exact clicks when Windows warns you, because the installer is not code-signed, are in the README under [On Windows: the installer](../README.md#on-windows-the-installer). Only do that for a file from this repository's releases page. At the end, Jig opens on its set-up page.

Jig keeps its settings, memories and notes in `%LOCALAPPDATA%\Jig`. Closing the window leaves Jig running. To turn it off, use **Turn Jig off** on the icon by the clock. To remove it: **Settings > Apps > Installed apps > Jig > Uninstall**.

A newer version is not installed on its own. In **Settings > About and updates**, choose **Check for updates**, read what is new, and, for this installer, choose **Install update**. The same check is on the icon by the clock. Details are in [Updates](updates.md).

**Mac, Linux, or Windows without the installer.** You need Python 3.11 or newer and Git.

```powershell
git clone https://github.com/rlesueur/jig.git
cd jig
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jig serve
```

On Mac and Linux, use `.venv/bin/` instead of `.\.venv\Scripts\`. Leave that terminal open: closing it stops Jig. Stop Jig with Ctrl+C, or `jig stop` in another window. Open it again with `jig ui`.

## 2. First run

`jig serve` opens Jig, already signed in: in its own window on Windows, and in your browser on Mac and Linux (add `--browser` on Windows to use the browser). A fresh checkout's `jig.toml` points at llama.cpp on `http://127.0.0.1:8080/v1`. If that model passes Jig's checks, the agent starts. Otherwise Jig opens on its set-up page and the agent stays off until a model passes.

What you choose on the set-up page is saved in the data folder's `settings.toml`. `jig.toml` itself is not rewritten.

Check the same things any time with `jig health`.

## 3. Point Jig at a local model

On the set-up page, Jig looks for a server it already knows:

| App | Address it looks for |
| --- | --- |
| llama.cpp (`llama-server`) | `http://127.0.0.1:8080/v1` |
| LM Studio | `http://127.0.0.1:1234/v1` |
| Ollama | `http://127.0.0.1:11434/v1` |

You can type another address. The model must support tool calling, and a context of 32K tokens is advisable (Jig warns if the server reports less, and still runs). How to load a model in each app, and the one setup Jig has measured, are in the README under [Get a model running](../README.md#get-a-model-running).

From a terminal, start Jig with a profile instead of editing `jig.toml`:

```powershell
.\.venv\Scripts\jig --config profiles/lmstudio.toml serve
.\.venv\Scripts\jig --config profiles/ollama.toml serve
.\.venv\Scripts\jig --config profiles/vllm.toml serve
```

`profiles/llamacpp-bonsai.toml` is a llama.cpp example (Ternary Bonsai 2 27B on the PrismML fork, which Jig was developed on). It is an example, not a requirement. vLLM needs its tool-calling flags; see that profile and [Choosing a model server](../README.md#choosing-a-model-server).

Common set-up messages, in Jig's own words, are listed in the README under [If something goes wrong](../README.md#if-something-goes-wrong). If port 8766 is taken, start with `jig serve --port 8767`. If Jig is already running for this data folder, open it with `jig ui`.

## 4. Or bring a cloud model

Jig supports OpenAI, OpenRouter, Anthropic and Google Gemini. You need an API key, and the provider charges you. Your conversation, the memories added to a message, tool results and any images you share are sent to the provider. Memories, notes, the audit log, rules and the vault stay on your machine.

On the set-up page, or in **Settings > Model and connection**, choose **Change model…**, then **Use a cloud model instead**. Pick the provider, paste the key, read what will be sent, and agree. Jig checks the key and the model before it switches.

From a terminal (Anthropic shown; use `openai`, `openrouter` or `gemini` the same way):

```powershell
.\.venv\Scripts\jig --config profiles/anthropic.toml model key set anthropic
.\.venv\Scripts\jig --config profiles/anthropic.toml model cloud confirm
.\.venv\Scripts\jig --config profiles/anthropic.toml serve
```

`jig model cloud status` shows whether you have agreed. `jig model cloud revoke` withdraws that. `jig model key status` and `jig model key delete <provider>` manage the key in the vault. The full rules (HTTPS only, what is sent, the safety checker on the same model) are in the README under [Using a cloud model](../README.md#using-a-cloud-model).

## 5. Once Jig is running

Chat from Jig's window. **Settings** is where you change the model, connect accounts, turn on search, and add schedules. The other guides are listed in [docs/README.md](README.md).

On Windows, **Start when I sign in** is off unless you turn it on under **Settings > Starting with Windows**. The Mac and Linux start-at-login files are generated, and have not been run on a real Mac or Linux machine in this beta.

## Attaching files

In the message box, use **Attach a file**, or drop a file onto the box, or paste an image. Jig accepts PNG, JPEG, Word (`.docx`), PDF (`.pdf`), plain text (`.txt`) and Markdown (`.md`). It checks the file itself, not only the name, and it tells you if a file is the wrong kind, too large, or not valid.

A picture is shown to the model only when vision is on (**[vision] enabled = true**, and a model that can see images). If vision is off, or the model cannot see images, Jig says so and does not pretend it looked. A Word document, PDF, text or Markdown file is read as text. Jig tells the model the file name and type, and treats the contents as information, not as instructions. A long document is taken in parts. Files stay with that conversation, so you can ask about one again later. Deleting the conversation deletes them.

From a Word document, Jig reads the body (paragraphs, headings, lists and tables) and also headers, footers, comments, text boxes, footnotes and endnotes, each with a label such as "Header:" or "Comment by Ada:". Pictures in the document, including in a header, a footer or a text box, are sent to the model as well, marked `[Picture 1]`, `[Picture 2]` and so on at the point they appear. From a PDF, Jig reads the text page by page, with each page marked `[Page 1]`, `[Page 2]` and so on. A picture on a page that also has text, such as a chart, a photo or a diagram, is sent the same way. A page with no text, such as a scan, is sent as a picture of the whole page. Jig does not run OCR. It shows the picture to the model, which reads it.

A PDF can be up to 8 MB and 100 pages. A PDF that asks for a password is refused. At most 4 pictures from documents are sent in one go. Scanned pages, pictures on a PDF page, and pictures in a Word document share that limit. Attached PNG and JPEG files do not. Jig says when only the first of them were sent. To see the rest, ask Jig to read the later PDF pages, or the later picture numbers in a Word document. A picture smaller than 64 pixels on both sides is treated as decoration and is not sent. EMF and WMF pictures are not sent, and Jig names each one it skipped. PNG, JPEG, GIF, BMP and TIFF pictures are sent. This needs vision on, the same as an attached PNG. If vision is off, or the model cannot see images, Jig says so and does not pretend it read the scan or the picture.
