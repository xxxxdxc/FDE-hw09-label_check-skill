// Local macOS Vision adapter. Emits text, confidence and normalized boxes.
// Coordinates use Vision's bottom-left origin. No image data leaves the Mac.
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("usage: vision_ocr.swift IMAGE\n", stderr)
    exit(2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
try VNImageRequestHandler(url: url, options: [:]).perform([request])
let items = (request.results ?? []).compactMap { observation -> [String: Any]? in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    return [
        "text": candidate.string,
        "confidence": Double(candidate.confidence),
        "bbox": [observation.boundingBox.origin.x, observation.boundingBox.origin.y,
                 observation.boundingBox.width, observation.boundingBox.height]
    ]
}
let data = try JSONSerialization.data(withJSONObject: items, options: [.sortedKeys])
print(String(data: data, encoding: .utf8) ?? "[]")
