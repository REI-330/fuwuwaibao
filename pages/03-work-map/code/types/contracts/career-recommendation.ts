export type CareerRecommendation = {
  occupation_id: string;
  occupation_name: string;
  match_score: number;
  reason: string;
  core_skills: string[];
  skill_gaps: string[];
  salary_range: string;
  future_signal: string;
  career_path: string;
};

export type CareerRecommendationsResponse = {
  recommendations: CareerRecommendation[];
};
