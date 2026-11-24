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

export type Member = { user_id: string; email: string; display_name: string; role: Role };
export type Grant = { user_id: string; email: string; display_name: string; role: Role };

// --- databases --------------------------------------------------------------------------

export type PropertyType =
  | "title"
  | "text"
  | "number"
  | "select"
  | "multi_select"
  | "date"
  | "checkbox"
  | "url"
  | "person"
  | "relation"
  | "rollup";

export type SelectOption = { id: string; name: string; color: string };

export type PropertyConfig = {
  options?: SelectOption[];
  format?: "number" | "percent" | "currency";
  database_id?: string;
  reverse_property_id?: string;
  reverse_of?: string;
  relation_property_id?: string;
  target_property_id?: string;
  function?: RollupFunction;
};

export type RollupFunction =
  | "show_original"
  | "count"
  | "count_values"
  | "count_unique"
  | "sum"
  | "average"
  | "min"
  | "max"
  | "percent_checked";

export type Property = { id: string; name: string; type: PropertyType; config: PropertyConfig; position: number };

export type FilterOp =
  | "equals"
  | "not_equals"
  | "contains"
  | "not_contains"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "is_empty"
  | "is_not_empty";

export type ViewFilter = { property_id: string; op: FilterOp; value?: unknown };
export type ViewSort = { property_id: string; direction: "asc" | "desc" };
export type ViewConfig = {
  sorts: ViewSort[];
  filters: ViewFilter[];
  group_by: string | null;
  hidden: string[];
  widths: Record<string, number>;
};
export type DatabaseView = { id: string; name: string; type: "table" | "board"; config: ViewConfig; position: number };

export type RelationRef = { id: string; title: string | null };
/** Values by property id, as the API returns them (relations and people as refs). */
export type RowValues = Record<string, unknown>;
export type Row = { id: string; position: number; values: RowValues; created_at: string; updated_at: string };

export type SearchResult = {
  kind: "page" | "row";
  page_id: string;
  row_id?: string;
  type: PageType | "row";
  title: string;
  database_title?: string;
  snippet: string | null;
  my_role: Role;
};
