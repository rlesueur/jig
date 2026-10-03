# MCP servers

MCP is how you add tools Jig does not ship. An MCP server is a program on this computer. In **Settings > MCP servers** you name that program and, if it needs them, its arguments, one per line. Jig starts the program itself. It does not use a shell, and it does not connect to an MCP server over the internet. A host chosen by the model, a tool or the server would sit outside the allow-list every other outbound call uses.

This is a beta (version 0.1.0b1). The same steps are on **Settings > MCP servers**. Adding a server, storing a secret, refreshing the tool list and removing a server only work on the computer Jig runs on. From another device Jig answers that the change only works on the host computer itself, not over the tailnet.

The safety rules for these tools are in the README under [MCP servers](../README.md#mcp-servers). This page is how to set one up.

## Set up an MCP server

1. **What an MCP server is.** It is a program on this computer that offers extra tools. Jig lists those tools and can call them, through the same gate as its own tools. Local programs only. Jig does not speak MCP over HTTP or SSE.

   A tool has to say what it does. It can put that in `_meta.jig` (`effect`, `outbound`, `category`) or in `annotations`. MCP's own hints count too: `readOnlyHint` and `destructiveHint` for the effect, and `openWorldHint` for whether the tool leaves this computer. A tool that does not say what it does is treated as able to send, change or delete, so Jig asks you first and the safety checker reviews it. A rule that says allow cannot skip that.

2. **Enter the program and its arguments.** The program is the thing Jig starts, such as `node` or a full path to a program. Arguments are one per line. Jig does not run them through a shell, so a name that is only a script (on Windows, `npx` is `npx.CMD`) is refused, and the page says so.

   **Read only** hides tools that are not reads, and refuses them if they are called. **Read and act** can use them, and still asks you before anything that could send, change or delete.

3. **Add any secrets in the vault.** If the program needs a token, do not put it in the program or the arguments. After the server is saved, the server's own box takes an environment variable name (letters, digits and underscores, not starting with a digit) and the secret. The value goes straight into the vault on this computer and is never shown again, never put in the chat, the logs or the audit log, and no tool can name it with `{{secret:...}}`. Jig starts the program again so it can see the variable. Refresh tools does the same.

4. **Save and check the tools.** Choose **Add server**. Jig starts the program and lists its tools under the server. Each line is the tool's name and how Jig will treat it. A line that says "does not say what it does, so Jig asks first" means the tool declared no effect. Jig asks you before it runs, and the safety checker reviews it.

   If Jig cannot find the program, or the server does not start, that message is shown on the server, in red, and under the steps. The server stays in the list so you can correct it: change nothing in the saved command from this page (remove it and add it again), and choose **Refresh tools** after you have fixed the program on disk.

5. **Remove a server.** Choose **Remove** on that server. Jig asks you to confirm, stops the program, and deletes the server. Its secrets are deleted from the vault. There is no separate switch to leave a server saved but turned off. Removing it is how you stop Jig using it.

## A folder Jig can read and change

This is the official filesystem server, `@modelcontextprotocol/server-filesystem`. People often start it with `npx -y @modelcontextprotocol/server-filesystem`, then the folder. Jig does not start `npx` on Windows, because that name is a script and Jig does not use a shell. Install the package, then point Jig at `node` and the server's own file. The last argument is the only folder that server may use.

1. Install it once, in a folder of its own. You need Node.js.

   ```powershell
   mkdir C:\Users\you\mcp-filesystem
   cd C:\Users\you\mcp-filesystem
   npm install @modelcontextprotocol/server-filesystem
   ```

   On Mac or Linux the same `npm install` works. Use the path it prints for `dist/index.js` below, with forward slashes.

2. In **Settings > MCP servers**, step 2:

   - **Name:** `Files`
   - **Program:** `node`
   - **Arguments**, one per line:

     ```text
     C:\Users\you\mcp-filesystem\node_modules\@modelcontextprotocol\server-filesystem\dist\index.js
     C:\Users\you\Notes
     ```

   - **What Jig may do:** Read and act

   `C:\Users\you\Notes` is the folder you are happy for that program to read and change. It has to exist. Use your own path.

3. Choose **Add server**. Under Files, Jig lists the tools. `list_directory` and `read_file` are reads (`readOnlyHint`). `write_file` is a side effect (`destructiveHint`), so Jig asks you before it runs. None of them say they leave this computer (`openWorldHint` is false).

4. This server does not need a secret, so step 3 has nothing to store.

5. Ask Jig to write a note in that folder with the Files server. The approval card names the MCP tool. Choose **Yes** and the file is written in the folder you gave, and only there. Choose **No** and Jig does not write it.

To stop using it, choose **Remove** on Files.

## If something goes wrong

Jig says what happened, on the server and under the steps. The wording comes from Jig:

- **Jig couldn't find that program.** The program name is not a program Jig can start. Give the name you would type in a terminal, or the full path. On Windows, `npx` on its own hits the script message below, not this one, when `npx.CMD` is on the PATH.
- **Jig couldn't start that program because it is a script, and Jig does not use a shell.** The name is a `.cmd`, `.bat` or `.ps1` file (or Windows found one of those for the name you typed). Jig will not start it: a script runs through a shell, and an argument could be read as shell syntax. Set the program to `node` and put the server's JavaScript file on the first argument line, as in the example above.
- **Jig found that file, but it is not a program it can start.** The path exists, and it is not a program (for example the bash script named `npx` with no extension). Use `node` and the JavaScript file.
- **could not start the MCP server (PermissionError).** The program is there, and this account may not run it.
- **the MCP server did not answer initialize.** The program started and did not speak MCP in time. A first run of a package that still has to download will do this. Install it first, as above, then add the server.
- **the MCP server closed its output.** The program started and then quit. Check the arguments, including the folder, which has to exist.
- **the MCP server speaks protocol 1.0, which Jig does not.** The words in the middle are whatever the server sent, or "an unrecognised one" when that text is not a short version string. Jig speaks `2025-06-18`, `2025-03-26` and `2024-11-05`.
- **give the server a name, up to 60 characters.** The name was empty or too long. Nothing was added.
- **say which program to run, as one line.** The program was empty or contained a line break. Nothing was added.
- **an environment variable's name must be letters, digits and underscores, and not start with a digit.** The secret was not stored.
- **the secret must be text, and it must not be empty.** The secret was not stored.

Adding an MCP server only works on the host computer itself, not over the tailnet. The same is true of storing a secret, refreshing and removing.
