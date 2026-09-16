// Shared TypeScript types mirroring the RepoLens backend schemas.

export type AnalysisStatus =
  | "pending"
  | "cloning"
  | "indexing"
  | "ready"
  | "failed";

export type Confidence = "DIRECT" | "INFERRED" | "UNRESOLVED" | "EXTERNAL";

export type RelationshipResolutionStatus = "RESOLVED" | "UNRESOLVED" | "EXTERNAL";

export type RelationshipType =
  | "IMPORTS"
  | "EXPORTS"
  | "DEFINES"
  | "CALLS"
  | "REFERENCES"
  | "EXTENDS"
  | "IMPLEMENTS"
  | "TESTS"
  | "DEPENDS_ON";

export interface RepositoryInfo {
  id: number;
  url: string;
  owner: string;
  name: string;
  branch: string | null;
  commit_sha: string | null;
  status: AnalysisStatus;
  file_count: number;
  source_size_bytes: number;
  symbol_count: number;
  relationship_count: number;
  analyzed: boolean;
  analyzed_at: string | null;
  parsed_file_count: number;
  import_count: number;
  export_count: number;
  languages: Record<string, number>;
  error_message: string | null;
  created_at: string | null;
}

export interface FileNode {
  path: string;
  name: string;
  is_dir: boolean;
  language: string | null;
  size: number;
}

export interface FileContent {
  path: string;
  content: string;
  language: string | null;
  line_count: number;
}

export interface AnalysisProgress {
  step: string;
  label: string;
  progress_percent: number;
}

export interface SymbolInfo {
  id: number;
  name: string;
  qualified_name: string | null;
  kind: string;
  language: string | null;
  file_path: string;
  line_start: number;
  line_end: number;
  start_column: number;
  end_column: number;
  exported: boolean;
  signature: string | null;
  docstring: string | null;
  parent_symbol_id: number | null;
}

export interface ImportInfo {
  id: number;
  file_path: string;
  source: string;
  imported_name: string | null;
  alias: string | null;
  kind: string;
  start_line: number;
  end_line: number;
}

export interface ExportInfo {
  id: number;
  file_path: string;
  name: string;
  kind: string;
  start_line: number;
  end_line: number;
}

export interface ParseStatusBreakdown {
  parsed: number;
  syntax_error: number;
  unsupported: number;
  failed: number;
}

export interface AnalysisSummary {
  repository_id: number;
  analyzed: boolean;
  analyzed_at: string | null;
  file_count: number;
  files_processed: number;
  symbol_count: number;
  import_count: number;
  export_count: number;
  statuses: ParseStatusBreakdown;
  parser_versions: Record<string, string>;
  duration_ms: number;
}

export interface SymbolsResponse {
  items: SymbolInfo[];
  total: number;
}

export interface RelationshipInfo {
  id: number;
  source_symbol_id: number | null;
  target_symbol_id: number | null;
  type: RelationshipType;
  resolution_status: RelationshipResolutionStatus;
  source_type: string;
  target_type: string;
  source_file: string | null;
  source_line: number;
  target_file: string | null;
  target_line: number | null;
  evidence: string | null;
  source_symbol_name: string | null;
  target_symbol_name: string | null;
}

export interface RelationshipSummary {
  repository_id: number;
  status: string;
  relationships_created: number;
  resolved: number;
  external: number;
  unresolved: number;
  duration_ms: number;
}

export interface RelationshipsResponse {
  items: RelationshipInfo[];
  total: number;
}

export interface SearchResult {
  symbol_id: number;
  name: string;
  kind: string;
  file_path: string;
  line: number;
  signature: string | null;
  preview_text: string;
}

export type SearchType = "symbol" | "file" | "text";

export interface SymbolSearchHit {
  symbol_id: number;
  name: string;
  qualified_name: string | null;
  kind: string;
  language: string | null;
  file_path: string;
  line_start: number;
  line_end: number;
  exported: boolean;
  signature: string | null;
}

export interface FileSearchHit {
  file_id: number;
  path: string;
  language: string | null;
  size_bytes: number;
  line_count: number;
  symbol_count: number;
}

export interface TextSearchHit {
  file_id: number;
  path: string;
  line_number: number;
  snippet: string;
  symbol_count: number | null;
}

export type SearchHit = SymbolSearchHit | FileSearchHit | TextSearchHit;

export interface SearchResponse {
  query: string;
  type: SearchType;
  total: number;
  results: SearchHit[];
}

export interface RelatedFileInfo {
  file_id: number;
  path: string;
  language: string | null;
  line_count: number;
  size_bytes: number;
  symbol_count: number;
  relationship_count: number;
  roles: string[];
}

export interface SourceContext {
  file_id: number | null;
  path: string | null;
  language: string | null;
  start_line: number;
  end_line: number;
  line_count: number;
  snippet: string | null;
}

export interface InvestigationGroup {
  category: string;
  label: string;
  direction: "incoming" | "outgoing" | "context";
  count: number;
  edges: RelationshipInfo[];
}

export interface InvestigationResponse {
  repository_id: number;
  symbol: SymbolInfo;
  file: RelatedFileInfo;
  groups: InvestigationGroup[];
  unresolved: RelationshipInfo[];
  external: RelationshipInfo[];
  related_files: RelatedFileInfo[];
  source_context: SourceContext;
}

// --- Impact simulation (Phase 6) ----------------------------------------------

export type ImpactClass =
  | "DIRECT"
  | "POTENTIAL"
  | "TESTS"
  | "UNRESOLVED"
  | "EXTERNAL";

export type ImpactNodeType = "SYMBOL" | "FILE" | "UNRESOLVED" | "EXTERNAL";

export interface ImpactInput {
  max_depth?: number;
}

export interface ImpactSummary {
  changed_symbols: number;
  changed_files: number;
  direct: number;
  potential: number;
  affected_tests: number;
  unresolved: number;
  external: number;
  file_level_changes: number;
  nodes_total: number;
  truncated: boolean;
  truncated_reason: string | null;
}

export interface ImpactNodeInfo {
  name: string;
  node_type: ImpactNodeType;
  kind: string;
  file_path: string | null;
  file_category: string | null;
  impact_class: ImpactClass;
  depth: number;
  lines: string | null;
  via: string | null;
  via_source: string | null;
  evidence_file: string | null;
  evidence_line: number;
  is_test: boolean;
  is_file_level_change: boolean;
  change_type: string | null;
}

export interface ImpactStepInfo {
  source: string;
  relationship: string;
  target: string;
  evidence: string;
}

export interface ImpactPathInfo {
  root: string;
  target: string;
  target_node_type: string;
  target_node_id: number;
  depth: number;
  steps: ImpactStepInfo[];
}

export interface ImpactAnalysisInfo {
  analysis_id: number;
  repository_id: number;
  diff_id: number;
  base_revision: string;
  head_revision: string;
  max_depth: number;
  summary: ImpactSummary;
  changed: ImpactNodeInfo[];
  potentially_affected: ImpactNodeInfo[];
  tests: ImpactNodeInfo[];
  unresolved: ImpactNodeInfo[];
  external: ImpactNodeInfo[];
  paths: ImpactPathInfo[];
}

// --- Git Diff (Phase 5) -------------------------------------------------------

export type DiffFileStatus =
  | "ADDED"
  | "MODIFIED"
  | "DELETED"
  | "RENAMED"
  | "COPIED"
  | "TYPE_CHANGED"
  | "UNTRACKED";

export type DiffFileCategory =
  | "SOURCE"
  | "TEST"
  | "CONFIG"
  | "DOCUMENTATION"
  | "UNKNOWN";

export type DiffSymbolChangeType = "ADDED" | "MODIFIED" | "DELETED";

export interface DiffInfo {
  id: number;
  repository_id: number;
  base_revision: string;
  head_revision: string;
  files_changed: number;
  insertions: number;
  deletions: number;
  symbols_changed: number;
  computed_at: string | null;
}

export interface DiffFileInfo {
  id: number;
  path: string;
  status: DiffFileStatus;
  old_path: string | null;
  new_path: string | null;
  additions: number;
  deletions: number;
  binary: boolean;
  file_category: DiffFileCategory;
  symbols_changed: number;
  symbol_change_types: Record<string, number>;
}

export interface DiffSymbolInfo {
  id: number;
  symbol_id: number;
  file_path: string;
  symbol_name: string;
  symbol_kind: string;
  change_type: DiffSymbolChangeType;
  added_lines: number;
  deleted_lines: number;
}

export interface DiffChangedLineInfo {
  side: "OLD" | "NEW";
  line_number: number;
  change_type: "ADDED" | "DELETED";
  text?: string | null;
}

export interface DiffHunkInfo {
  id: number;
  header: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DiffChangedLineInfo[];
}

export interface DiffDetail extends DiffInfo {
  files: DiffFileInfo[];
  symbols: DiffSymbolInfo[];
}

export interface DiffHunkList {
  file: DiffFileInfo;
  hunks: DiffHunkInfo[];
}

// --- Change Review workflow (Phase 7) -----------------------------------------

export type ReviewItemType =
  | "CHANGED_CODE"
  | "AFFECTED_CALLER"
  | "AFFECTED_DEPENDENCY"
  | "AFFECTED_TEST"
  | "UNRESOLVED_IMPACT"
  | "EXTERNAL_DEPENDENCY"
  | "CONFIGURATION_CHANGE"
  | "DOCUMENTATION_CHANGE";

export type ReviewPriority = "REQUIRED" | "RECOMMENDED" | "INFORMATIONAL";

export type ReviewItemStatus = "OPEN" | "IN_PROGRESS" | "DONE" | "SKIPPED";

export type ReviewEntryStatus = "OPEN" | "DONE";

export interface ReviewEntryInfo {
  entry_id: number;
  key: string;
  title: string;
  description: string;
  kind: string;
  status: ReviewEntryStatus;
  symbol_id: number | null;
  file_path: string | null;
  evidence_file: string | null;
  evidence_line: number;
  path_steps: ImpactStepInfo[];
}

export interface ReviewItemInfo {
  item_id: number;
  item_type: ReviewItemType;
  title: string;
  description: string;
  status: ReviewItemStatus;
  priority: ReviewPriority;
  source_type: string | null;
  source_id: number | null;
  evidence_file: string | null;
  evidence_start_line: number;
  evidence_end_line: number;
  group_via: string | null;
  notes: string;
  entries: ReviewEntryInfo[];
}

export interface ReviewSummaryInfo {
  total_items: number;
  required: number;
  recommended: number;
  informational: number;
  completed: number;
  open: number;
  in_progress: number;
  skipped: number;
}

export interface ReviewInfo {
  review_id: number;
  repository_id: number;
  diff_id: number;
  impact_analysis_id: number;
  title: string;
  summary: string;
  status: ReviewItemStatus;
  base_revision: string;
  head_revision: string;
  max_depth: number;
  created_at: string | null;
  updated_at: string | null;
  review_summary: ReviewSummaryInfo;
  items: ReviewItemInfo[];
}

// --- Lens AI explanation layer (Phase 8) ---------------------------------------

export type LensKind = "change" | "impact" | "review" | "unresolved";

export type LensEvidenceKind =
  | "changed-symbol"
  | "changed-file"
  | "diff-hunk"
  | "potentially-affected"
  | "test"
  | "external"
  | "unresolved-call"
  | "review-item"
  | "review-entry"
  | "impact-path"
  | "warning"
  | string;

export interface LensEvidenceInfo {
  index: number;
  kind: LensEvidenceKind;
  impact: string | null;
  label: string;
  file: string | null;
  detail: string | null;
  line: number | null;
  node_id: number | null;
  symbol_id: number | null;
  item_id: number | null;
  depth: number | null;
  snippet: string | null;
}

export interface LensResponse {
  kind: LensKind;
  provider: string;
  model: string;
  prompt_version: string;
  summary: string;
  evidence: LensEvidenceInfo[];
  uncertainty: string | null;
  suggested_checks: string[];
}

export interface LensChangeRequest {
  diff_id: number;
  diff_file_id?: number;
}

export interface LensImpactRequest {
  analysis_id: number;
}

export interface LensReviewRequest {
  review_id: number;
}

export interface LensUnresolvedRequest {
  analysis_id: number;
}

// --- Overview recent activity (Phase 8) ----------------------------------------

export interface RecentActivityInfo {
  diffs: DiffInfo[];
  impact_analyses: ImpactAnalysisInfo[];
  reviews: ReviewInfo[];
}

export const EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";
