export type Role = "owner" | "editor" | "commenter" | "viewer";
export type PageType = "document" | "database" | "mindmap" | "folder";

export type User = { id: string; email: string; display_name: string };
export type Person = { id: string; display_name: string };

export type Workspace = { id: string; name: string; role: Role | null; created_at: string };

export type Page = {
  id: string;
  workspace_id: string;
  parent_id: string | null;
  type: PageType;
  title: string;
  position: number;
  my_role: Role;
  created_at: string;
  updated_at: string;
};

export type Version = {
  id: string;
  label: string | null;
  created_at: string;
  created_by: Person | null;
  size_bytes: number;
  state?: string;
};

export type CommentAnchor = { from: string; to: string };

export type Comment = {
  id: string;
  thread_id: string | null;
  author: Person | null;
  body: string;
  anchor: CommentAnchor | null;
  quote: string | null;
  resolved_at: string | null;
  resolved_by: Person | null;
  created_at: string;
  updated_at: string;
};

export type StoredFile = {
  id: string;
  page_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
  url?: string;
};

const RANK: Record<Role, number> = { viewer: 0, commenter: 1, editor: 2, owner: 3 };

export function atLeast(role: Role | null | undefined, minimum: Role): boolean {
  return role != null && RANK[role] >= RANK[minimum];
}
