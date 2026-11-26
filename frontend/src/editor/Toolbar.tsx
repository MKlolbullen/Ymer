import { useEditorState, type Editor } from "@tiptap/react";
import {
  Bold,
  ChartColumn,
  Code,
  Heading1,
  Heading2,
  Heading3,
  ImageUp,
  Italic,
  Link,
  List,
  ListOrdered,
  ListTodo,
  MessageSquarePlus,
  Minus,
  Redo2,
  SquareCode,
  Strikethrough,
  TextQuote,
  Undo2,
} from "lucide-react";
import { useRef, type ReactNode } from "react";

import { AssistMenu } from "@/ai/AssistMenu";
import { Icon } from "@/components/icons";

type Props = {
  editor: Editor;
  workspaceId: string;
  pageId: string;
  canEdit: boolean;
  canComment: boolean;
  onComment: () => void;
  onFiles: (files: File[]) => void;
};

function Button({
  label,
  active,
  disabled,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()} // keep the editor's selection
      onClick={onClick}
      className="inline-flex h-8 min-w-7 items-center justify-center rounded px-1 text-sm text-zinc-600 hover:bg-zinc-100 disabled:opacity-40 aria-pressed:bg-zinc-200 aria-pressed:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:aria-pressed:bg-zinc-700 dark:aria-pressed:text-white"
    >
      {children}
    </button>
  );
}

const Divider = () => <span className="mx-1 h-5 w-px bg-zinc-200 dark:bg-zinc-700" aria-hidden />;

export function Toolbar({ editor, workspaceId, pageId, canEdit, canComment, onComment, onFiles }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      strike: e.isActive("strike"),
      code: e.isActive("code"),
      link: e.isActive("link"),
      h1: e.isActive("heading", { level: 1 }),
      h2: e.isActive("heading", { level: 2 }),
      h3: e.isActive("heading", { level: 3 }),
      bullet: e.isActive("bulletList"),
      ordered: e.isActive("orderedList"),
      task: e.isActive("taskList"),
      quote: e.isActive("blockquote"),
      codeBlock: e.isActive("codeBlock"),
      hasSelection: !e.state.selection.empty,
      canUndo: e.can().undo?.() ?? false,
      canRedo: e.can().redo?.() ?? false,
    }),
  });
  const chain = () => editor.chain().focus();

  const setLink = () => {
    if (state.link) return chain().unsetLink().run();
    const href = window.prompt("Link URL");
    if (href) chain().extendMarkRange("link").setLink({ href }).run();
  };

  return (
    <div
      role="toolbar"
      aria-label="Formatting"
      className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 border-b border-zinc-200 bg-white/95 px-2 py-1 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95"
    >
      <AssistMenu editor={editor} workspaceId={workspaceId} pageId={pageId} canEdit={canEdit} />
      {canEdit && (
        <>
          <Button label="Undo" disabled={!state.canUndo} onClick={() => chain().undo().run()}><Icon icon={Undo2} /></Button>
          <Button label="Redo" disabled={!state.canRedo} onClick={() => chain().redo().run()}><Icon icon={Redo2} /></Button>
          <Divider />
          <Button label="Heading 1" active={state.h1} onClick={() => chain().toggleHeading({ level: 1 }).run()}><Icon icon={Heading1} /></Button>
          <Button label="Heading 2" active={state.h2} onClick={() => chain().toggleHeading({ level: 2 }).run()}><Icon icon={Heading2} /></Button>
          <Button label="Heading 3" active={state.h3} onClick={() => chain().toggleHeading({ level: 3 }).run()}><Icon icon={Heading3} /></Button>
          <Divider />
          <Button label="Bold" active={state.bold} onClick={() => chain().toggleBold().run()}><Icon icon={Bold} /></Button>
          <Button label="Italic" active={state.italic} onClick={() => chain().toggleItalic().run()}><Icon icon={Italic} /></Button>
          <Button label="Strikethrough" active={state.strike} onClick={() => chain().toggleStrike().run()}><Icon icon={Strikethrough} /></Button>
          <Button label="Inline code" active={state.code} onClick={() => chain().toggleCode().run()}><Icon icon={Code} /></Button>
          <Button label="Link" active={state.link} onClick={setLink}><Icon icon={Link} /></Button>
          <Divider />
          <Button label="Bullet list" active={state.bullet} onClick={() => chain().toggleBulletList().run()}><Icon icon={List} /></Button>
          <Button label="Numbered list" active={state.ordered} onClick={() => chain().toggleOrderedList().run()}><Icon icon={ListOrdered} /></Button>
          <Button label="Task list" active={state.task} onClick={() => chain().toggleTaskList().run()}><Icon icon={ListTodo} /></Button>
          <Button label="Quote" active={state.quote} onClick={() => chain().toggleBlockquote().run()}><Icon icon={TextQuote} /></Button>
          <Button label="Code block" active={state.codeBlock} onClick={() => chain().toggleCodeBlock().run()}><Icon icon={SquareCode} /></Button>
          <Button label="Divider" onClick={() => chain().setHorizontalRule().run()}><Icon icon={Minus} /></Button>
          <Button label="Chart from a database" onClick={() => chain().insertChart().run()}><Icon icon={ChartColumn} /></Button>
          <Divider />
          <Button label="Upload image or file" onClick={() => fileInput.current?.click()}><Icon icon={ImageUp} /></Button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              onFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </>
      )}
      {canComment && (
        <>
          {canEdit && <Divider />}
          <Button label="Comment on selection" disabled={!state.hasSelection} onClick={onComment}>
            <Icon icon={MessageSquarePlus} /><span className="ml-1.5 hidden sm:inline">Comment</span>
          </Button>
        </>
      )}
      {!canEdit && (
        <span className="ml-auto px-2 text-xs text-zinc-500">
          {canComment ? "You can comment on this page" : "View only"}
        </span>
      )}
    </div>
  );
}
