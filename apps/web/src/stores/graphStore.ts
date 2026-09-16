import { create } from "zustand";

/**
 * Graph page selection state:
 * - selectedNodeId: node shown in the right side panel.
 * - mergeSelection: up to two creative node ids chosen for merging
 *   (via shift-click on creative nodes or the panel checkbox).
 */
interface GraphSelectionState {
  selectedNodeId: string | null;
  mergeSelection: string[];
  selectNode: (id: string | null) => void;
  toggleMergeSelection: (id: string) => void;
  clearMergeSelection: () => void;
}

export const useGraphStore = create<GraphSelectionState>()((set) => ({
  selectedNodeId: null,
  mergeSelection: [],
  selectNode: (id) => set({ selectedNodeId: id }),
  toggleMergeSelection: (id) =>
    set((state) => ({
      mergeSelection: state.mergeSelection.includes(id)
        ? state.mergeSelection.filter((existing) => existing !== id)
        : [...state.mergeSelection, id].slice(-2),
    })),
  clearMergeSelection: () => set({ mergeSelection: [] }),
}));
