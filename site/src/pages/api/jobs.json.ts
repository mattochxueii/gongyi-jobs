import type { APIRoute } from 'astro';
import {
  extractPublicSalaryHint,
  hasExplicitSalary,
  loadSiteData,
} from '../../lib/data';
import type { JobItem } from '../../lib/types';

function serializePublicJob(job: JobItem) {
  const pm = job.public_meta ?? null;
  const salaryHint = extractPublicSalaryHint(job.summary, pm?.other_requirements);
  return {
    id: job.id,
    category: job.category,
    title: job.title,
    org_name: job.org_name,
    province: job.province,
    city: job.city,
    district: job.district ?? null,
    source_url: job.source_url,
    apply_url: job.apply_url ?? null,
    source_name: job.source_name,
    source_type: job.source_type,
    published_at: job.published_at ?? null,
    deadline_at: job.deadline_at ?? null,
    status: job.status,
    summary: job.summary,
    tags: job.tags ?? [],
    salary_hint: salaryHint,
    public_meta: pm
      ? {
          post_code: pm.post_code ?? null,
          department: pm.department ?? null,
          post_level: pm.post_level ?? null,
          bianzhi_type: pm.bianzhi_type,
          headcount: pm.headcount ?? null,
          education_min: pm.education_min,
          degree_min: pm.degree_min ?? null,
          majors: pm.majors ?? [],
          fresh_grad_only:
            typeof pm.fresh_grad_only === 'boolean' ? pm.fresh_grad_only : null,
          age_limit: pm.age_limit ?? null,
          political_status: pm.political_status ?? null,
          exam_type: pm.exam_type ?? null,
          apply_method: pm.apply_method ?? null,
          apply_start_at: pm.apply_start_at ?? null,
          written_exam_time: pm.written_exam_time ?? null,
          interview_time: pm.interview_time ?? null,
          other_requirements: pm.other_requirements ?? null,
          announcement_title: pm.announcement_title ?? null,
          attachments: (pm.attachments ?? []).map((att) => ({
            name: att.name,
            url: att.url,
            file_type: att.file_type ?? null,
          })),
        }
      : null,
  };
}

function serializeEnterpriseJob(job: JobItem) {
  const em = job.enterprise_meta ?? null;
  return {
    id: job.id,
    category: job.category,
    title: job.title,
    org_name: job.org_name,
    province: job.province,
    city: job.city,
    district: job.district ?? null,
    source_url: job.source_url,
    apply_url: job.apply_url ?? null,
    source_name: job.source_name,
    source_type: job.source_type,
    published_at: job.published_at ?? null,
    deadline_at: job.deadline_at ?? null,
    status: job.status,
    summary: job.summary,
    tags: job.tags ?? [],
    enterprise_meta: em
      ? {
          company_id: em.company_id,
          work_schedule: em.work_schedule,
          work_hours: em.work_hours ?? null,
          salary_range:
            typeof em.salary_range === 'string' && em.salary_range.trim().length > 0
              ? em.salary_range.trim()
              : null,
          remote_policy: em.remote_policy,
          department: em.department ?? null,
          experience_req: em.experience_req ?? null,
          education_req: em.education_req ?? null,
          benefits_verified: (em.benefits_verified ?? []).map((b) => ({
            tag: b.tag,
            evidence_quote: b.evidence_quote,
            evidence_url: b.evidence_url ?? null,
          })),
        }
      : null,
  };
}

export const GET: APIRoute = () => {
  const data = loadSiteData();
  const publicJobs = data.jobs.filter((j) => j.category === 'public_institution');
  const enterpriseJobs = data.jobs.filter((j) => j.category === 'enterprise');

  const payload = {
    schema_version: '1.0',
    updated_at: data.latestUpdatedAt,
    is_preview_fixtures: data.isPreviewFixtures,
    counts: {
      public_total: publicJobs.length,
      public_active: publicJobs.filter((j) => j.status === 'active').length,
      companies_total: data.companies.length,
      companies_active: data.companies.filter((c) => c.status === 'active').length,
      enterprise_jobs_total: enterpriseJobs.length,
      enterprise_jobs_with_salary: enterpriseJobs.filter(hasExplicitSalary).length,
    },
    public_jobs: publicJobs.map(serializePublicJob),
    companies: data.companies,
    enterprise_jobs: enterpriseJobs.map(serializeEnterpriseJob),
  };

  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
    },
  });
};
