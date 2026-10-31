"""One-to-one conveyor matching with order preserved within transverse lanes."""


def ordered_matches(edges, tracks, detections, *, axis, lane_distance):
    """Maximize gated matches, then minimize prediction error, without overtakes.

    Each lane is a connected interval of transverse observations. Different
    lanes are solved independently. Large/dense groups fail closed rather than
    allocate an unbounded quadratic table on the board.
    """
    if not edges:
        return []
    longitudinal = 'centroid_'+axis
    transverse = 'centroid_'+('y' if axis == 'x' else 'x')
    by_id = {track.track_id: track for track in tracks}
    costs = {(edge[1], edge[2]): edge for edge in edges}
    track_ids = {tid for tid, _ in costs}
    detection_ids = {index for _, index in costs}
    points = sorted([(getattr(by_id[tid].detection, transverse), 0, tid) for tid in track_ids]
                    + [(getattr(detections[index], transverse), 1, index) for index in detection_ids])
    lanes = []
    for point in points:
        if not lanes or point[0]-lanes[-1][-1][0] > lane_distance:
            lanes.append([])
        lanes[-1].append(point)
    chosen = []
    for lane in lanes:
        tids = sorted((index for _, kind, index in lane if kind == 0),
                      key=lambda tid:(getattr(by_id[tid].detection, longitudinal), tid))
        dids = sorted((index for _, kind, index in lane if kind == 1),
                      key=lambda index:(getattr(detections[index], longitudinal), index))
        if not tids or not dids or len(tids)*len(dids) > 16384:
            continue
        scores = [[(0, 0.) for _ in range(len(dids)+1)] for _ in range(len(tids)+1)]
        actions = [['' for _ in range(len(dids)+1)] for _ in range(len(tids)+1)]
        for i, tid in enumerate(tids, 1):
            for j, did in enumerate(dids, 1):
                best, action = scores[i-1][j], 'track'
                if scores[i][j-1] > best:
                    best, action = scores[i][j-1], 'detection'
                edge = costs.get((tid, did))
                if edge is not None:
                    old = scores[i-1][j-1]
                    match = (old[0]+1, old[1]-edge[0])
                    if match > best:
                        best, action = match, 'match'
                scores[i][j], actions[i][j] = best, action
        i, j = len(tids), len(dids)
        while i and j:
            action = actions[i][j]
            if action == 'match':
                chosen.append(costs[tids[i-1], dids[j-1]])
                i -= 1
                j -= 1
            elif action == 'track':
                i -= 1
            else:
                j -= 1
    return chosen
