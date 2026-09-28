/**
 * The first-run screen (`docs/DESIGN.md` 8.5): what `/` renders on a workspace that holds
 * nothing yet. Stills: `docs/design/counterpart-firstrun-light.png` and its dark twin.
 *
 * The screen exists because the activation metric is time-to-first-record and a bare empty
 * state -- "No object types have been created yet." in a dashed box -- offers nothing to do and
 * no way to do it. What it offers instead is a prompt: the product's premise is that an
 * agent builds the schema, so the first screen hands the operator the sentence that starts that.
 *
 * **Two things about it are deliberate departures from the still, recorded rather than silent.**
 * The still's footer also offers "Start with a template", and no template gallery exists.
 * And the still is framed without a sidebar, while every other still has one; this renders
 * inside the shell like every other route, because the theme control and sign-out live there and
 * nowhere else, and a second shell variant would need its own collapsed form below 960px.
 *
 * **Who sees it** is the role, not the empty list. See `IndexRoute`.
 */
import { Link } from "react-router-dom";

import { useWorkspace } from "../hooks/useWorkspace";
import { PromptBlock } from "../ui/PromptBlock";
import { FIRST_RUN_PROMPT_MONO, firstRunPrompt, resolveMcpUrl } from "./mcpUrl";

export function FirstRun() {
  const { data: workspace } = useWorkspace();
  // While the workspace read is in flight this composes from the origin, which is the same
  // answer the fallback gives and never a wrong one: the URL is replaced, not corrected, when
  // a deployment that declared a base URL answers.
  const mcpUrl = resolveMcpUrl(workspace?.mcp_url, window.location.origin);
  const prompt = firstRunPrompt(mcpUrl);

  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-6 px-6 py-16 text-center">
      {/* The second clause carries the agent colour because the sentence is addressed to an
          agent (docs/DESIGN.md 3; the reference stills sample to exactly --color-agent-ink in
          both themes). Split inside the heading, so it stays one accessible name. */}
      <h1 className="font-display text-5xl font-semibold tracking-tighter text-ink">
        What do you want to <span className="text-agent-ink">keep track of?</span>
      </h1>

      <p className="max-w-xl text-lg text-ink-2">
        Nothing is set up yet, and that is the point. Paste this into the agent you already use.
        It will build the types and fields, explain each one, and offer to add your first records.
      </p>

      <div className="w-full">
        <PromptBlock text={prompt} mono={FIRST_RUN_PROMPT_MONO(mcpUrl)} />
      </div>

      <p className="text-base text-ink-2">
        No agent yet? You can{" "}
        <Link to="/schema/new" className="text-human-ink underline-offset-2 hover:underline">
          set up a type by hand
        </Link>
        .
      </p>
    </div>
  );
}
