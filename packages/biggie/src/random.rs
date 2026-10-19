use rand::{RngExt, SeedableRng, distr::Alphanumeric, rngs::StdRng};

pub fn rng_from_seed(seed: Option<u64>) -> StdRng {
    seed.map_or_else(|| StdRng::from_rng(&mut rand::rng()), StdRng::seed_from_u64)
}

// Rejection sampling preserves the draw order and uniform distribution of the
// remaining alphabet. Callers own their buffering and requested byte count.
pub fn alphanumeric_bytes(
    rng: &mut impl RngExt,
    excluded: Option<u8>,
) -> impl Iterator<Item = u8> + '_ {
    std::iter::repeat_with(|| rng.sample(Alphanumeric)).filter(move |byte| Some(*byte) != excluded)
}
