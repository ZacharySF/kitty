/**
 * The job a "Tailor resume" press hands to the Resume Workshop it opens: read once, by the
 * next Workshop to mount, which opens that job's tailored copy (else the master) on Tailor
 * with the job linked.
 */
export interface TailorJob {
  id: number;
  company: string;
  title: string;
}

let pending: TailorJob | null = null;

export function tailorForJob(job: TailorJob): void {
  pending = job;
}

export function takeTailorJob(): TailorJob | null {
  const job = pending;
  pending = null;
  return job;
}
