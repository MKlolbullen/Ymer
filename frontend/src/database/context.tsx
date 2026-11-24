import { createContext, useContext } from "react";

import type { useDatabaseMutations } from "@/lib/queries";
import type { Property, Row, SelectOption } from "@/lib/types";

export type DatabaseContextValue = {
  workspaceId: string;
  databaseId: string;
  properties: Property[];
  canEdit: boolean;
  mutations: ReturnType<typeof useDatabaseMutations>;
  /** Write one value; relation values are written as row ids. */
  setValue: (row: Row, property: Property, value: unknown) => void;
  /** Add an option to a select property and return it. */
  addOption: (property: Property, name: string) => Promise<SelectOption>;
  openRow: (rowId: string) => void;
};

export const DatabaseContext = createContext<DatabaseContextValue | null>(null);

export function useDatabaseContext(): DatabaseContextValue {
  const value = useContext(DatabaseContext);
  if (!value) throw new Error("useDatabaseContext outside a database");
  return value;
}
