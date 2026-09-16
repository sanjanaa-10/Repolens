import axios from "axios";
import type {
  AnalysisProgress,
  AnalysisSummary,
  DiffDetail,
  DiffFileInfo,
  DiffHunkList,
  DiffInfo,
  DiffSymbolInfo,
  ExportInfo,
  FileContent,
  FileNode,
  ImpactAnalysisInfo,
  ImpactInput,
  ImportInfo,
  InvestigationResponse,
  LensChangeRequest,
  LensImpactRequest,
  LensResponse,
  LensReviewRequest,
  LensUnresolvedRequest,
  RecentActivityInfo,
  RelationshipsResponse,
  RelationshipSummary,
  RepositoryInfo,
  ReviewEntryInfo,
  ReviewInfo,
  ReviewItemInfo,
  ReviewItemStatus,
  ReviewEntryStatus,
  SearchResponse,
  SymbolInfo,
  SymbolsResponse,
} from "../types";

const client = axios.create({
  baseURL: "/api",
  timeout: 120_000,
});

// --- Infrastructure ----------------------------------------------------------

export async function healthCheck(): Promise<{
  status: string;
  service: string;
  version: string;
}> {
  const { data } = await client.get("/health");
  return data;
}

// --- Ingestion ---------------------------------------------------------------

export async function ingestRepository(url: string): Promise<RepositoryInfo> {
  const { data } = await client.post("/repositories", { url }, { timeout: 300_000 });
  return data;
}

export async function getRepositories(): Promise<RepositoryInfo[]> {
  const { data } = await client.get("/repositories");
  return data;
}

export async function getRepository(id: number): Promise<RepositoryInfo> {
  const { data } = await client.get(`/repositories/${id}`);
  return data;
}

export async function getAnalysisStatus(
  id: number
): Promise<{ status: string; progress: AnalysisProgress[] }> {
  const { data } = await client.get(`/repositories/${id}/status`);
  return data;
}

export async function getFiles(
  id: number,
  path?: string
): Promise<FileNode[]> {
  const { data } = await client.get(`/repositories/${id}/files`, {
    params: { path: path ?? "" },
  });
  return data;
}

export async function deleteRepository(id: number): Promise<void> {
  await client.delete(`/repositories/${id}`);
}

// --- Analysis (deterministic parse pipeline) ---------------------------------

export async function parseRepository(id: number): Promise<AnalysisSummary> {
  const { data } = await client.post(
    `/repositories/${id}/parse`,
    undefined,
    { timeout: 600_000 }
  );
  return data;
}

export async function getAnalysis(id: number): Promise<AnalysisSummary> {
  const { data } = await client.get(`/repositories/${id}/analysis`);
  return data;
}

export interface SymbolQuery {
  file?: string;
  name?: string;
  kind?: string;
  language?: string;
  limit?: number;
  offset?: number;
}

export async function getSymbols(
  id: number,
  params: SymbolQuery = {}
): Promise<SymbolsResponse> {
  const { data } = await client.get(`/repositories/${id}/symbols`, {
    params,
  });
  return data;
}

export async function getImports(
  id: number,
  params: { file?: string; limit?: number; offset?: number } = {}
): Promise<ImportInfo[]> {
  const { data } = await client.get(`/repositories/${id}/imports`, {
    params,
  });
  return data;
}

export async function getExports(
  id: number,
  params: { file?: string; limit?: number; offset?: number } = {}
): Promise<ExportInfo[]> {
  const { data } = await client.get(`/repositories/${id}/exports`, {
    params,
  });
  return data;
}

// --- Investigation -----------------------------------------------------------

export async function getFileContent(
  id: number,
  path: string
): Promise<FileContent> {
  const { data } = await client.get(`/repositories/${id}/content`, {
    params: { path },
  });
  return data;
}

export async function search(
  id: number,
  query: string,
  type: "symbol" | "file" | "text" = "symbol",
  limit = 30,
  offset = 0,
  caseSensitive = false
): Promise<SearchResponse> {
  const { data } = await client.get(`/repositories/${id}/search`, {
    params: {
      q: query,
      type,
      limit,
      offset,
      case_sensitive: caseSensitive,
    },
  });
  return data;
}

export async function investigateSymbol(
  id: number,
  symbolId: number
): Promise<InvestigationResponse> {
  const { data } = await client.get(
    `/repositories/${id}/symbols/${symbolId}/investigation`
  );
  return data;
}

export async function getSymbolDetail(
  id: number,
  symbolId: number
): Promise<SymbolInfo> {
  const { data } = await client.get(`/repositories/${id}/symbols/${symbolId}`);
  return data;
}

// --- Relationships -----------------------------------------------------------

export interface RelationshipQuery {
  type?: string;
  status?: string;
  file?: string;
  source?: string;
  target?: string;
  limit?: number;
  offset?: number;
}

export async function buildRelationships(
  id: number
): Promise<RelationshipSummary> {
  const { data } = await client.post(`/repositories/${id}/relationships`, undefined, {
    timeout: 600_000,
  });
  return data;
}

export async function getRelationships(
  id: number,
  params: RelationshipQuery = {}
): Promise<RelationshipsResponse> {
  const { data } = await client.get(`/repositories/${id}/relationships`, {
    params,
  });
  return data;
}

export async function getSymbolNeighborhood(
  id: number,
  symbolId: number
): Promise<RelationshipsResponse> {
  const { data } = await client.get(
    `/repositories/${id}/symbols/${symbolId}/relationships`
  );
  return data;
}

// --- Impact simulation (Phase 6) ----------------------------------------------

export async function simulateImpact(
  repositoryId: number,
  diffId: number,
  input: ImpactInput = {}
): Promise<ImpactAnalysisInfo> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/diff/${diffId}/impact`,
    input,
    { timeout: 300_000 }
  );
  return data;
}

export async function getImpactAnalysis(
  repositoryId: number,
  analysisId: number
): Promise<ImpactAnalysisInfo> {
  const { data } = await client.get(
    `/repositories/${repositoryId}/impact/${analysisId}`
  );
  return data;
}

// --- Change Review workflow (Phase 7) ----------------------------------------

export async function createReview(
  repositoryId: number,
  analysisId: number,
  regenerate = false
): Promise<ReviewInfo> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/impact/${analysisId}/review`,
    { regenerate }
  );
  return data;
}

export async function getReview(
  repositoryId: number,
  reviewId: number
): Promise<ReviewInfo> {
  const { data } = await client.get(
    `/repositories/${repositoryId}/reviews/${reviewId}`
  );
  return data;
}

export async function updateReviewItem(
  repositoryId: number,
  reviewId: number,
  itemId: number,
  patch: { status?: ReviewItemStatus; notes?: string }
): Promise<ReviewItemInfo> {
  const { data } = await client.patch(
    `/repositories/${repositoryId}/reviews/${reviewId}/items/${itemId}`,
    patch
  );
  return data;
}

export async function updateReviewEntry(
  repositoryId: number,
  reviewId: number,
  itemId: number,
  entryId: number,
  status: ReviewEntryStatus
): Promise<ReviewEntryInfo> {
  const { data } = await client.patch(
    `/repositories/${repositoryId}/reviews/${reviewId}/items/${itemId}/entries/${entryId}`,
    { status }
  );
  return data;
}

// --- Git Diff (Phase 5) -------------------------------------------------------

export async function createDiff(
  id: number,
  baseRevision: string,
  headRevision: string
): Promise<DiffInfo> {
  const { data } = await client.post(`/repositories/${id}/diff`, {
    base_revision: baseRevision,
    head_revision: headRevision,
  });
  return data;
}

export async function getDiffDetail(
  id: number,
  diffId: number
): Promise<DiffDetail> {
  const { data } = await client.get(`/repositories/${id}/diff/${diffId}`);
  return data;
}

export async function getDiffFiles(
  id: number,
  diffId: number
): Promise<DiffFileInfo[]> {
  const { data } = await client.get(
    `/repositories/${id}/diff/${diffId}/files`
  );
  return data;
}

export async function getDiffSymbols(
  id: number,
  diffId: number
): Promise<DiffSymbolInfo[]> {
  const { data } = await client.get(
    `/repositories/${id}/diff/${diffId}/symbols`
  );
  return data;
}

export async function getDiffFileHunks(
  id: number,
  diffId: number,
  diffFileId: number
): Promise<DiffHunkList> {
  const { data } = await client.get(
    `/repositories/${id}/diff/${diffId}/files/${diffFileId}`
  );
  return data;
}

// --- Overview recent activity (Phase 8) ---------------------------------------

export async function getRecentActivity(repositoryId: number): Promise<RecentActivityInfo> {
  const { data } = await client.get(`/repositories/${repositoryId}/recent`);
  return data;
}

// --- Lens AI explanation layer (Phase 8) --------------------------------------

export async function lensChange(
  repositoryId: number,
  request: LensChangeRequest
): Promise<LensResponse> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/lens/change`,
    request,
    { timeout: 60_000 }
  );
  return data;
}

export async function lensImpact(
  repositoryId: number,
  request: LensImpactRequest
): Promise<LensResponse> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/lens/impact`,
    request,
    { timeout: 60_000 }
  );
  return data;
}

export async function lensReview(
  repositoryId: number,
  request: LensReviewRequest
): Promise<LensResponse> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/lens/review`,
    request,
    { timeout: 60_000 }
  );
  return data;
}

export async function lensUnresolved(
  repositoryId: number,
  request: LensUnresolvedRequest
): Promise<LensResponse> {
  const { data } = await client.post(
    `/repositories/${repositoryId}/lens/unresolved`,
    request,
    { timeout: 60_000 }
  );
  return data;
}

// Maps HTTP error responses to user-facing Lens states.
export function lensErrorState(error: unknown): {
  unavailable: boolean;
  failed: boolean;
  message: string;
} {
  const status = (error as { response?: { status?: number } })?.response?.status;
  if (status === 503) {
    return {
      unavailable: true,
      failed: false,
      message:
        "AI explanations are optional. Deterministic analysis — diff, impact, review, and exploration — is fully available without Lens.",
    };
  }
  if (status === 502) {
    return {
      unavailable: false,
      failed: true,
      message:
        (error as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Lens could not generate an explanation.",
    };
  }
  return {
    unavailable: false,
    failed: true,
    message: "Lens is temporarily unavailable. Please try again.",
  };
}